"""
Language registry contract (M4 Phase B).

Language identity was a bare string repeated in four places that drifted
apart, and the drift caused three production bugs:

  1. `LANGUAGE_IDS` held "js" but not "javascript" while the serializer
     accepted "javascript" — every JavaScript submission failed.
  2. `hidden_wrapper_code` keys disagreed across seed generations, which is
     why the wrapper alias table exists at all.
  3. `ALLOWED_LANGUAGES` omitted "c" entirely, so the serializer rejected
     every C submission — while Judge0 had an id for it, the content
     pipeline generated C stubs, and the frontend offered C in the picker.
     A student could choose C, write a solution and be told the language was
     unsupported. Found while writing this module.

The consistency tests below are the point of the whole exercise: they are
what would have caught all three, and they fail if any consumer drifts from
the registry again.
"""

import pytest

from common import languages
from groups.coding_views import LANGUAGE_IDS
from groups.serializers import ALLOWED_LANGUAGES, CodeSubmitSerializer
from groups.services import WRAPPER_LANGUAGE_ALIASES


# ── the registry itself ──────────────────────────────────────────────────

def test_every_spelling_resolves_to_exactly_one_language():
    seen = {}
    for lang in languages.REGISTRY:
        for spelling in lang.spellings:
            assert spelling not in seen, (
                f"{spelling!r} claimed by both {seen.get(spelling)} and {lang.key} — "
                f"one would silently shadow the other"
            )
            seen[spelling] = lang.key


def test_resolution_is_case_and_whitespace_insensitive():
    for value in ["Python", "  python  ", "PYTHON"]:
        assert languages.canonical(value) == "python"


def test_unknown_and_empty_input_resolve_to_nothing():
    for value in ["rust", "", None, "   "]:
        assert languages.get(value) is None
        assert languages.canonical(value) is None
        assert languages.judge0_id(value) is None
        assert languages.is_supported(value) is False


def test_js_and_javascript_are_the_same_language():
    """Bug 1, pinned."""
    assert languages.canonical("js") == languages.canonical("javascript")
    assert languages.judge0_id("js") == languages.judge0_id("javascript") == 63


def test_c_is_supported():
    """Bug 3, pinned. This is the assertion that was false before Phase B."""
    assert languages.is_supported("c")
    assert languages.judge0_id("c") == 50


def test_self_contained_languages_are_exactly_c_and_cpp():
    # C/C++ have no generic wrapper, so the student writes a whole program.
    assert languages.SELF_CONTAINED == {"c", "cpp"}
    assert languages.is_self_contained("c++") is True      # via alias
    assert languages.is_self_contained("python") is False


def test_wrapper_spellings_try_canonical_first():
    # Seed data files templates under any accepted spelling; canonical first
    # so the most likely key is checked before the legacy one.
    assert languages.wrapper_spellings("js")[0] == "javascript"
    assert "js" in languages.wrapper_spellings("javascript")


def test_wrapper_spellings_degrades_for_unknown_languages():
    # wrapper_for() must keep working for a language the registry has never
    # heard of rather than raising inside grading.
    assert languages.wrapper_spellings("rust") == ("rust",)
    assert languages.wrapper_spellings(None) == ()


# ── consistency across every consumer ────────────────────────────────────

def test_judge0_map_covers_every_accepted_spelling():
    """Bug 1's shape: the serializer accepting what the runner cannot map."""
    for spelling in ALLOWED_LANGUAGES:
        assert spelling in LANGUAGE_IDS, (
            f"serializer accepts {spelling!r} but LANGUAGE_IDS cannot map it — "
            f"submissions in that language will fail as 'Unsupported language'"
        )


def test_serializer_accepts_every_spelling_the_runner_can_execute():
    """Bug 3's shape: the runner supporting what the serializer rejects."""
    for spelling in LANGUAGE_IDS:
        assert spelling in ALLOWED_LANGUAGES, (
            f"Judge0 can run {spelling!r} but the serializer rejects it — "
            f"the UI can offer a language that cannot be submitted"
        )


def test_wrapper_aliases_cover_every_accepted_spelling():
    for spelling in ALLOWED_LANGUAGES:
        assert spelling in WRAPPER_LANGUAGE_ALIASES


def test_all_three_maps_derive_from_one_registry():
    assert set(ALLOWED_LANGUAGES) == set(LANGUAGE_IDS) == set(WRAPPER_LANGUAGE_ALIASES)
    assert set(ALLOWED_LANGUAGES) == set(languages.ACCEPTED_SPELLINGS)


# ── the serializer, end to end ───────────────────────────────────────────

@pytest.mark.django_db
class TestSerializerLanguageValidation:
    def _validate(self, language, question_id):
        s = CodeSubmitSerializer(data={
            "problem_id": question_id, "code": "x = 1", "language": language,
        })
        return s.is_valid(), s.errors

    @pytest.fixture
    def question_id(self):
        from groups.models import CodingPortal, Question, Topic
        portal = CodingPortal.objects.create(name="P")
        topic = Topic.objects.create(name="T", portal=portal)
        return Question.objects.create(
            topic=topic, title="Q", content="c", base_difficulty=1200.0,
            hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        ).pk

    @pytest.mark.parametrize("language", ["python", "java", "cpp", "c", "js", "javascript"])
    def test_every_supported_language_is_accepted(self, language, question_id):
        valid, errors = self._validate(language, question_id)
        assert valid, f"{language} rejected: {errors}"

    def test_c_submissions_are_accepted(self, question_id):
        """
        The user-visible bug this closes: the frontend offered C with a
        self-contained main() skeleton, reseed_questions generated C stubs,
        and Judge0 had an id — but the serializer rejected every C
        submission with "Unsupported language: c".
        """
        valid, errors = self._validate("c", question_id)
        assert valid, errors

    def test_language_is_normalised_to_lowercase(self, question_id):
        s = CodeSubmitSerializer(data={
            "problem_id": question_id, "code": "x = 1", "language": "Python",
        })
        assert s.is_valid(), s.errors
        assert s.validated_data["language"] == "python"

    def test_unsupported_languages_are_still_rejected(self, question_id):
        valid, errors = self._validate("rust", question_id)
        assert not valid
        assert "language" in errors
