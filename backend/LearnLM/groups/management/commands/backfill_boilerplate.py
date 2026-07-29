"""
Backfills missing starter-code languages on already-seeded questions.

Questions reseeded before the multi-language change only carry Python
starter code. This command asks the LLM for ONLY the missing language
stubs (java/cpp/javascript), mirroring the existing Python starter — about
5x cheaper per question than a full regeneration, so the whole bank fits
in ~4 quota-days instead of ~18.

Resume is automatic: questions that already have all target languages are
skipped, so re-running continues where the quota cut you off. Stops
cleanly when the provider's daily token cap is hit.

Usage:
    python manage.py backfill_boilerplate --dry-run --limit 2
    python manage.py backfill_boilerplate
    python manage.py backfill_boilerplate --topic "Array" --limit 200
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection, OperationalError

from groups.models import Question
from groups.ai_services import generate_starter_stubs, DailyQuotaExhausted

logger = logging.getLogger("backfill_boilerplate")

# Keep in sync with studysphere-ai-11/src/components/LanguageSelector.tsx —
# "c" was added there without ever being generated anywhere in the content
# pipeline, so every question showed an empty C tab.
TARGET_LANGUAGES = ("java", "cpp", "javascript", "c")


class Command(BaseCommand):
    help = "Adds missing java/cpp/javascript starter code to seeded questions. Cheap stub-only LLM calls."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Generate and show stubs without writing to the database.")
        parser.add_argument("--limit", type=int, default=None,
                            help="Only process the first N matching questions.")
        parser.add_argument("--delay", type=float, default=1.5,
                            help="Seconds to sleep between AI calls. Default 1.5s.")
        parser.add_argument("--topic", type=str, default=None,
                            help="Only process questions whose topic name contains this string.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        delay = options["delay"]
        topic_filter = options["topic"]

        qs = Question.objects.exclude(content__icontains=Question.PLACEHOLDER_MARKER).order_by("id")
        if topic_filter:
            qs = qs.filter(topic__name__icontains=topic_filter)

        # Candidates: python starter present, at least one target language missing.
        # JSONField shape queries are backend-fiddly, so filter in Python.
        candidates = []
        for q in qs.only("id", "title", "boilerplate_code"):
            boiler = q.boilerplate_code or {}
            python_starter = boiler.get("python", "")
            if not (isinstance(python_starter, str) and python_starter.strip()):
                continue
            missing = [lang for lang in TARGET_LANGUAGES if not boiler.get(lang)]
            if missing:
                candidates.append((q.id, missing))
            if limit and len(candidates) >= limit:
                break

        total = len(candidates)
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to backfill — all seeded questions have full language coverage."))
            return

        mode = "DRY RUN — no writes" if dry_run else "LIVE RUN"
        self.stdout.write(self.style.WARNING(f"{total} question(s) need starter-code backfill. Mode: {mode}"))

        done = 0
        failed = 0
        for i, (q_id, missing) in enumerate(candidates, start=1):
            # AI calls are slow; force a fresh DB connection for the write.
            # Never close inside an atomic block (e.g. under test runners).
            if not connection.in_atomic_block:
                connection.close()
            q = Question.objects.get(id=q_id)
            title = q.title.strip()
            self.stdout.write(f"[{i}/{total}] {title!r} — missing: {', '.join(missing)}")

            try:
                stubs = generate_starter_stubs(title, q.boilerplate_code.get("python", ""), missing)
            except DailyQuotaExhausted as e:
                self.stdout.write(self.style.ERROR(
                    f"\n🛑 Daily LLM token quota exhausted — stopping. Re-run later to resume.\n({e})"
                ))
                break

            time.sleep(delay)

            if not stubs:
                self.stdout.write(self.style.ERROR("  ❌ Generation failed or returned no valid stubs — will retry on next run."))
                failed += 1
                continue

            if dry_run:
                for lang, code in stubs.items():
                    preview = code.replace("\n", " ")[:100]
                    self.stdout.write(self.style.SUCCESS(f"  [DRY RUN] {lang}: {preview}..."))
                done += 1
                continue

            q.boilerplate_code = {**(q.boilerplate_code or {}), **stubs}
            if not self._save_with_retry(q):
                self.stdout.write(self.style.ERROR("  ❌ DB write failed after retry — will retry on next run."))
                failed += 1
                continue
            self.stdout.write(self.style.SUCCESS(f"  ✅ Added: {', '.join(stubs.keys())}"))
            done += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n🎉 Backfilled {done}/{total} question(s); {failed} failed (rerun picks them up)."
        ))

    # ------------------------------------------------------------------
    # The AI call this write follows can take several seconds, long enough
    # for Neon's pooled connection to be dropped underneath us
    # (psycopg.OperationalError: "server closed the connection
    # unexpectedly") — observed live in production. Retry once on a fresh
    # connection before giving up; the question is picked up again on the
    # command's next (auto-resuming) run either way.
    # ------------------------------------------------------------------
    def _save_with_retry(self, q):
        for attempt in range(2):
            try:
                q.save(update_fields=["boilerplate_code"])
                return True
            except OperationalError:
                logger.warning("DB connection error saving id=%s, attempt %d/2 — reconnecting", q.id, attempt + 1)
                connection.close()
        return False
