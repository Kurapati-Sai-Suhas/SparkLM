"""
reseed_questions — validation, retry, and coverage-visibility tests.

Covers the review-pass fixes: validation-inside-retry (a schema-invalid but
non-empty AI response must trigger a real retry, not a silent accept),
stdin/expected_output type-checking (a bare number must be rejected here
instead of crashing grading later), and --retry-failed re-checking the
placeholder marker (an already-resolved question must not be silently
reprocessed and re-billed).
"""

import json
from unittest.mock import patch

import pytest

from groups.management.commands.reseed_questions import Command
from groups.ai_services import DailyQuotaExhausted
from groups.models import CodingPortal, Question, Topic


def _valid_payload(**overrides):
    payload = {
        "content": "<p>A perfectly fine problem statement that is long enough.</p>",
        "starter_code": {
            "python": "class Solution:\n    def solve(self, x):\n        pass",
            "java": "class Solution { public int solve(int x) { return 0; } }",
            "cpp": "class Solution { public: int solve(int x) { return 0; } };",
            "javascript": "class Solution { solve(x) {} }",
            "c": "int solve(int x) { return 0; }",
        },
        "hidden_test_cases": [
            {"stdin": "1", "expected_output": "1"},
            {"stdin": "2", "expected_output": "2"},
        ],
    }
    payload.update(overrides)
    return payload


class TestValidateAiPayload:
    def setup_method(self):
        self.cmd = Command()

    def test_accepts_a_valid_payload(self):
        assert self.cmd._validate_ai_payload(_valid_payload()) is None

    def test_empty_string_expected_output_is_valid(self):
        # Regression guard for the original fix this file already shipped:
        # "" is a legitimate answer (e.g. Longest Common Prefix with none).
        payload = _valid_payload(hidden_test_cases=[
            {"stdin": "abc", "expected_output": ""},
            {"stdin": "xyz", "expected_output": "xy"},
        ])
        assert self.cmd._validate_ai_payload(payload) is None

    def test_rejects_missing_keys(self):
        payload = _valid_payload()
        del payload["hidden_test_cases"]
        assert "missing keys" in self.cmd._validate_ai_payload(payload)

    def test_rejects_non_string_stdin(self):
        payload = _valid_payload(hidden_test_cases=[
            {"stdin": 5, "expected_output": "5"},
            {"stdin": "6", "expected_output": "6"},
        ])
        error = self.cmd._validate_ai_payload(payload)
        assert error is not None and "must be strings" in error

    def test_rejects_non_string_expected_output(self):
        payload = _valid_payload(hidden_test_cases=[
            {"stdin": "5", "expected_output": 5},
            {"stdin": "6", "expected_output": "6"},
        ])
        error = self.cmd._validate_ai_payload(payload)
        assert error is not None and "must be strings" in error

    def test_rejects_null_stdin(self):
        payload = _valid_payload(hidden_test_cases=[
            {"stdin": None, "expected_output": "5"},
            {"stdin": "6", "expected_output": "6"},
        ])
        error = self.cmd._validate_ai_payload(payload)
        assert error is not None and "null" in error

    def test_rejects_too_few_test_cases(self):
        payload = _valid_payload(hidden_test_cases=[{"stdin": "1", "expected_output": "1"}])
        error = self.cmd._validate_ai_payload(payload)
        assert error is not None and "at least" in error

    def test_rejects_duplicate_inputs(self):
        payload = _valid_payload(hidden_test_cases=[
            {"stdin": "1", "expected_output": "1"},
            {"stdin": "1", "expected_output": "1"},
        ])
        error = self.cmd._validate_ai_payload(payload)
        assert error is not None and "identical" in error

    def test_python_only_starter_code_still_valid(self):
        # Only python is a hard requirement (see the module docstring for
        # why) -- java/cpp/javascript/c are best-effort here and any gaps
        # are closed later by backfill_boilerplate.
        payload = _valid_payload(starter_code={"python": "class Solution:\n    def solve(self): pass"})
        assert self.cmd._validate_ai_payload(payload) is None


class TestBonusLanguageSummary:
    def setup_method(self):
        self.cmd = Command()

    def test_reports_all_present(self):
        summary = self.cmd._bonus_language_summary(_valid_payload())
        assert "all present" in summary
        assert "c" in summary

    def test_reports_missing_languages(self):
        payload = _valid_payload(starter_code={"python": "x", "java": "y"})
        summary = self.cmd._bonus_language_summary(payload)
        assert "missing" in summary
        assert "cpp" in summary and "c" in summary


class TestGenerateWithRetry:
    def setup_method(self):
        self.cmd = Command()

    @patch("groups.management.commands.reseed_questions.generate_full_question")
    def test_retries_on_schema_invalid_response_and_succeeds(self, mock_gen):
        # First call returns well-formed JSON that is missing
        # hidden_test_cases entirely -- the pre-fix code treated any
        # non-empty dict as success and never retried. Second call is
        # fully valid; the fix must reach it.
        incomplete = {
            "content": "<p>ok but incomplete, long enough to pass the length check</p>",
            "starter_code": {"python": "x"},
        }
        mock_gen.side_effect = [incomplete, _valid_payload()]
        ai_data, error = self.cmd._generate_with_retry("Some Problem", delay=0)
        assert ai_data is not None
        assert error is None
        assert mock_gen.call_count == 2

    @patch("groups.management.commands.reseed_questions.generate_full_question")
    def test_gives_up_after_max_retries_and_reports_last_error(self, mock_gen):
        bad = {
            "content": "<p>ok but incomplete, long enough to pass the length check</p>",
            "starter_code": {"python": "x"},
        }
        mock_gen.return_value = bad
        ai_data, error = self.cmd._generate_with_retry("Some Problem", delay=0)
        assert ai_data is None
        assert error is not None and "missing keys" in error

    @patch("groups.management.commands.reseed_questions.generate_full_question")
    def test_quota_exhaustion_propagates_immediately_no_pointless_retries(self, mock_gen):
        mock_gen.side_effect = DailyQuotaExhausted("quota gone")
        with pytest.raises(DailyQuotaExhausted):
            self.cmd._generate_with_retry("Some Problem", delay=0)
        assert mock_gen.call_count == 1


@pytest.mark.django_db
class TestBuildQuerysetRetryFailed:
    def test_skips_already_resolved_questions(self, tmp_path):
        portal = CodingPortal.objects.create(name="Retry Test Portal")
        topic, _ = Topic.objects.get_or_create(
            name="Retry Topic", defaults={"structure_type": "flat", "portal": portal}
        )
        still_placeholder = Question.objects.create(
            topic=topic, title="Still Broken",
            content=Question.PLACEHOLDER_MARKER + " this one.",
            base_difficulty=1200.0,
        )
        already_fixed = Question.objects.create(
            topic=topic, title="Already Fixed",
            content="<p>A real, already-reseeded problem statement.</p>",
            base_difficulty=1200.0,
        )

        failure_file = tmp_path / "failures.json"
        failure_file.write_text(json.dumps([
            {"id": still_placeholder.id, "title": "Still Broken", "reason": "x"},
            {"id": already_fixed.id, "title": "Already Fixed", "reason": "x"},
        ]))

        cmd = Command()
        qs = cmd._build_queryset(topic_filter=None, retry_failed_path=str(failure_file))
        ids = set(qs.values_list("id", flat=True))
        assert ids == {still_placeholder.id}
