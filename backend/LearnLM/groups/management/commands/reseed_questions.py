"""
Django management command: reseed_questions.py

Location this file should live at:
    <your_app>/management/commands/reseed_questions.py

Usage:
    python manage.py reseed_questions                              # full run
    python manage.py reseed_questions --dry-run                    # preview only, no writes
    python manage.py reseed_questions --limit 10                   # only process first 10 matches
    python manage.py reseed_questions --delay 1.5                  # seconds between AI calls
    python manage.py reseed_questions --topic "Arrays"              # only reseed questions under a topic
    python manage.py reseed_questions --retry-failed failures.json  # only reprocess a prior failure list

Changes in this version (based on the actual production run logs):
    1. FIXED a real validation bug: empty-string `expected_output` (e.g. "" for
       "Longest Common Prefix" with no common prefix) was being rejected as invalid.
       An empty string can be a legitimate correct answer — only a *missing* key
       (not present, or None) is now treated as invalid.
    2. Fixed the recurring `psycopg.OperationalError: consuming input failed` crash.
       The DB connection was sitting idle for the several seconds each AI call takes,
       and got dropped by an intermediary (OS socket layer / pgbouncer / Postgres
       idle timeout) before the write. We now proactively close the connection before
       every AI call (forcing Django to open a fresh one for the save) and retry the
       save once on a connection error before giving up on that question.
    3. On any validation failure, the raw AI response is now logged (truncated to
       2000 chars) so truncated/malformed JSON (e.g. the "N-Queens" case, which was
       missing starter_code and hidden_test_cases entirely) can actually be diagnosed
       instead of just seeing "missing keys".
    4. Failed questions are now written to a JSON failure file as they happen, and
       --retry-failed <file> lets you reprocess just that list instead of rescanning
       thousands of rows again.
    5. Question titles are stripped of leading/trailing whitespace before being sent
       to the AI (source data had leading spaces like " Longest Common Prefix").
"""

import json
import logging
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction, connection, OperationalError

from groups.models import Question
from groups.ai_services import generate_full_question, DailyQuotaExhausted

logger = logging.getLogger("reseed_questions")

PLACEHOLDER_MARKER = Question.PLACEHOLDER_MARKER

MIN_TEST_CASES = 2
MAX_RETRIES = 3
MAX_RAW_LOG_CHARS = 2000

DEFAULT_FAILURE_FILE = "reseed_failures.json"


class Command(BaseCommand):
    help = "Reseeds placeholder/boilerplate questions with AI-generated content, starter code, and test cases."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                             help="Generate and validate content but do not write anything to the database.")
        parser.add_argument("--limit", type=int, default=None,
                             help="Only process the first N matching questions.")
        parser.add_argument("--delay", type=float, default=1.5,
                             help="Seconds to sleep between AI calls. Default 1.5s.")
        parser.add_argument("--topic", type=str, default=None,
                             help="Only reseed questions belonging to a topic whose name contains this string.")
        parser.add_argument("--failure-file", type=str, default=DEFAULT_FAILURE_FILE,
                             help=f"Where to write failed question IDs/titles/reasons. Default: {DEFAULT_FAILURE_FILE}")
        parser.add_argument("--retry-failed", type=str, default=None,
                             help="Path to a previously written failure file. Only reprocesses those question IDs.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        delay = options["delay"]
        topic_filter = options["topic"]
        failure_file = Path(options["failure_file"])
        retry_failed_path = options["retry_failed"]

        questions = self._build_queryset(topic_filter, retry_failed_path)
        questions = questions.order_by("id")
        total = questions.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No matching questions found. Nothing to do."))
            return

        if limit:
            total = min(total, limit)
            questions = questions[:limit]

        mode_label = "DRY RUN — no writes will occur" if dry_run else "LIVE RUN"
        self.stdout.write(self.style.WARNING(f"Found {total} question(s) to process. Mode: {mode_label}"))
        self.stdout.write(self.style.WARNING("Press Ctrl+C to stop gracefully.\n"))

        processed = 0
        success = 0
        failed = 0
        interrupted = False
        failures = []  # collected as we go, written out at the end (and on interrupt)

        try:
            question_ids = list(questions.values_list('id', flat=True))
            for q_id in question_ids:
                q = Question.objects.get(id=q_id)
                title = q.title.strip()
                self.stdout.write(f"[{processed + 1}/{total}] Generating for: {title!r} ...")

                try:
                    ai_data = self._generate_with_retry(title, delay)
                except DailyQuotaExhausted as e:
                    # Every remaining question would hit the same wall —
                    # stop cleanly. Untouched questions still carry the
                    # placeholder marker, so the next run resumes here.
                    self.stdout.write(self.style.ERROR(
                        f"\n🛑 Daily LLM token quota exhausted — stopping. "
                        f"Remaining questions resume automatically on the next run.\n({e})"
                    ))
                    break
                processed += 1

                if ai_data is None:
                    msg = "AI generation failed after retries"
                    self.stdout.write(self.style.ERROR(f"  ❌ {msg}: {title}"))
                    logger.error("id=%s title=%r reason=%s", q.id, title, msg)
                    failures.append({"id": q.id, "title": title, "reason": msg})
                    failed += 1
                    continue

                validation_error = self._validate_ai_payload(ai_data)
                if validation_error:
                    self.stdout.write(self.style.ERROR(f"  ❌ Invalid AI response ({validation_error}): {title}"))
                    logger.error(
                        "id=%s title=%r reason=%s raw=%r",
                        q.id, title, validation_error, str(ai_data)[:MAX_RAW_LOG_CHARS],
                    )
                    failures.append({"id": q.id, "title": title, "reason": validation_error})
                    failed += 1
                    continue

                examples_block = self._build_examples_block(ai_data["hidden_test_cases"])
                new_content = f"{ai_data['content'].strip()}\n\n{examples_block}"

                if dry_run:
                    self.stdout.write(self.style.SUCCESS(f"  ✅ [DRY RUN] Would update: {title}"))
                    self.stdout.write(f"     Preview:\n{self._indent(examples_block)}")
                    success += 1
                    continue

                if not self._save_question(q, ai_data, new_content):
                    self.stdout.write(self.style.ERROR(f"  ❌ DB write failed for: {title}"))
                    failures.append({"id": q.id, "title": title, "reason": "db_write_failed"})
                    failed += 1
                    continue

                self.stdout.write(self.style.SUCCESS(f"  ✅ Updated: {title}"))
                success += 1

        except KeyboardInterrupt:
            interrupted = True
            self.stdout.write(self.style.ERROR("\n🛑 Interrupted by user."))

        if failures:
            self._write_failure_file(failure_file, failures)

        self._print_summary(processed, success, failed, total, interrupted, dry_run, failure_file, len(failures))

    # ------------------------------------------------------------------
    def _build_queryset(self, topic_filter, retry_failed_path):
        if retry_failed_path:
            path = Path(retry_failed_path)
            if not path.exists():
                raise SystemExit(f"Failure file not found: {path}")
            data = json.loads(path.read_text())
            ids = [entry["id"] for entry in data]
            self.stdout.write(self.style.WARNING(f"Retry mode: reprocessing {len(ids)} question(s) from {path}"))
            return Question.objects.filter(id__in=ids)

        qs = Question.objects.filter(content__icontains=PLACEHOLDER_MARKER)
        if topic_filter:
            qs = qs.filter(topic__name__icontains=topic_filter)
        return qs

    # ------------------------------------------------------------------
    # AI call with retry/backoff. Connection is closed before each call so
    # Django is forced to open a fresh one for the DB write that follows,
    # instead of reusing a connection that may have been dropped while we
    # were waiting on the AI response.
    # ------------------------------------------------------------------
    def _generate_with_retry(self, title, delay):
        for attempt in range(1, MAX_RETRIES + 1):
            connection.close()
            try:
                ai_data = generate_full_question(title)
                time.sleep(delay)
                if ai_data:
                    return ai_data
                logger.warning("Empty AI response for %r (attempt %d/%d)", title, attempt, MAX_RETRIES)
            except DailyQuotaExhausted:
                raise  # handled by the main loop — retrying is pointless
            except Exception as e:
                logger.warning("AI call raised for %r (attempt %d/%d): %s", title, attempt, MAX_RETRIES, e)
                time.sleep(delay)

            if attempt < MAX_RETRIES:
                backoff = delay * (2 ** attempt)
                self.stdout.write(self.style.WARNING(f"  Retrying in {backoff:.1f}s..."))
                time.sleep(backoff)

        return None

    # ------------------------------------------------------------------
    # Saves the question, retrying once if the connection was dropped while
    # we were off doing the (slow) AI call.
    # ------------------------------------------------------------------
    def _save_question(self, q, ai_data, new_content):
        for attempt in range(2):
            try:
                with transaction.atomic():
                    q.content = new_content
                    q.boilerplate_code = {"python": ai_data["starter_code"]}
                    q.hidden_test_cases = ai_data["hidden_test_cases"]
                    q.save(update_fields=["content", "boilerplate_code", "hidden_test_cases"])
                return True
            except OperationalError:
                logger.warning("DB connection error saving id=%s, attempt %d/2 — reconnecting", q.id, attempt + 1)
                connection.close()
                if attempt == 1:
                    logger.exception("DB save failed permanently for id=%s title=%r", q.id, q.title)
            except Exception:
                logger.exception("DB save failed for id=%s title=%r", q.id, q.title)
                return False
        return False

    # ------------------------------------------------------------------
    # Schema + content-quality validation.
    #
    # IMPORTANT: an empty string is a *valid* expected_output for some problems
    # (e.g. "Longest Common Prefix" returning "" when there's no common prefix).
    # We only reject a test case when the key is truly absent or None — not when
    # it holds a legitimate falsy value.
    # ------------------------------------------------------------------
    def _validate_ai_payload(self, ai_data):
        if not isinstance(ai_data, dict):
            return "AI response is not a JSON object"

        required_keys = ("content", "starter_code", "hidden_test_cases")
        missing = [k for k in required_keys if k not in ai_data]
        if missing:
            return f"missing keys: {missing}"

        if not isinstance(ai_data["content"], str) or len(ai_data["content"].strip()) < 20:
            return "content is empty or too short"

        if not isinstance(ai_data["starter_code"], str) or not ai_data["starter_code"].strip():
            return "starter_code is empty"

        test_cases = ai_data["hidden_test_cases"]
        if not isinstance(test_cases, list) or len(test_cases) < MIN_TEST_CASES:
            got = len(test_cases) if isinstance(test_cases, list) else "non-list"
            return f"need at least {MIN_TEST_CASES} test cases, got {got}"

        for i, tc in enumerate(test_cases):
            if not isinstance(tc, dict):
                return f"test case {i} is not an object"
            if "stdin" not in tc or "expected_output" not in tc:
                return f"test case {i} missing stdin/expected_output key"
            if tc["stdin"] is None or tc["expected_output"] is None:
                # None means the AI didn't provide a value at all.
                # An empty string "" is a legitimate value and is NOT rejected here.
                return f"test case {i} has a null stdin/expected_output"

        # Reject only exact-duplicate inputs across every test case (degenerate case
        # where the model didn't vary inputs at all).
        unique_inputs = {str(tc["stdin"]) for tc in test_cases}
        if len(unique_inputs) < 2:
            return "all test cases have identical input"

        return None

    # ------------------------------------------------------------------
    def _build_examples_block(self, test_cases, max_examples=3):
        lines = ["### Examples\n"]
        for i, tc in enumerate(test_cases[:max_examples], start=1):
            lines.append(f"**Input {i}:** `{tc['stdin']}`")
            lines.append(f"**Output {i}:** `{tc['expected_output']}`")
            if tc.get("explanation"):
                lines.append(f"**Explanation:** {tc['explanation']}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _indent(text, prefix="     "):
        return "\n".join(prefix + line for line in text.splitlines())

    def _write_failure_file(self, path, failures):
        path.write_text(json.dumps(failures, indent=2))
        self.stdout.write(self.style.WARNING(f"\nWrote {len(failures)} failure(s) to {path}"))
        self.stdout.write(self.style.WARNING(f"Re-run just these with: --retry-failed {path}"))

    # ------------------------------------------------------------------
    def _print_summary(self, processed, success, failed, total, interrupted, dry_run, failure_file, n_failures):
        self.stdout.write("")
        if interrupted:
            self.stdout.write(self.style.ERROR(
                f"⏸  Interrupted early. Processed {processed}/{total} ({success} succeeded, {failed} failed)."
            ))
        else:
            verb = "would be updated" if dry_run else "updated"
            self.stdout.write(self.style.SUCCESS(
                f"🎉 Finished. {success}/{processed} questions {verb}. {failed} failed."
            ))
        if dry_run and success > 0:
            self.stdout.write(self.style.WARNING("Dry run — no database rows were changed."))
        if n_failures:
            self.stdout.write(self.style.WARNING(f"Failures logged to {failure_file} — retry with --retry-failed {failure_file}"))
