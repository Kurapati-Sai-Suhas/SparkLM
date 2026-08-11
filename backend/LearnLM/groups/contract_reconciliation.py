"""
Tier-A static execution-contract reconciliation (M2 P2.7a-1).

P2.6 shipped a versioned execution harness. P2.7's audit then proved the
harness is not the whole problem: `reseed_questions` writes space-separated
test data and never sets `execution_contract_version`, so reseeded questions
stay on v1, whose Python harness parses JSON per line. Measured with a
genuinely correct Two Sum solution against reseed's own data format:

    v1: exit 1, stdout ''      -> cannot pass
    v2: exit 0, stdout '0 1'   -> matches the stored expected_output

So a correct solution fails, and the fix exists but nothing reaches it. Before
a single hidden test is written, we need to know which questions are in that
state.

── Why this is static ───────────────────────────────────────────────────────

A question's REAL I/O convention is recoverable from its stored data alone:
`"2 7 11 15"` is not valid JSON, so a question containing it cannot be graded
by a harness that calls json.loads on each line. That is decidable by reading,
not by running.

Everything here is therefore pure and read-only: no Judge0, no oracle, no
learner code, no writes. It answers "which contract can grade this question's
stored data?" and nothing else. Oracle agreement, determinism and reference
approval are Tier B and need P2.7c plus a working Judge0.

── What it must never do ────────────────────────────────────────────────────

It RECOMMENDS; it never migrates. `V2_ONLY` means "this data appears
compatible with v2 and incompatible with v1" — NOT "flip the version". A real
migration needs boilerplate, an approved oracle, hidden tests and the
publishability contract, all of which are later phases.

It also never reports a hidden input, an expected output, or reference source.
Reports are designed to be archived by a scheduled job, and those three are
grading truth.
"""

import json

# ── Contract implied by the stored test data ────────────────────────────────
V1_ONLY = "V1_ONLY"                        # JSON-per-line in, JSON-ish out
V2_ONLY = "V2_ONLY"                        # tokens in, space-separated out
AMBIGUOUS_CONTRACT = "AMBIGUOUS_CONTRACT"  # both harnesses grade it identically
NEITHER = "NEITHER"                        # neither generic harness fits
CONTRACT_MISMATCH = "CONTRACT_MISMATCH"    # the cases disagree with each other
MISSING_TESTS = "MISSING_TESTS"
INVALID_TESTS = "INVALID_TESTS"
CUSTOM_WRAPPER = "CUSTOM_WRAPPER"          # governed by its own harness

# ── Boilerplate ─────────────────────────────────────────────────────────────
MISSING_BOILERPLATE = "MISSING_BOILERPLATE"
INVALID_BOILERPLATE = "INVALID_BOILERPLATE"

# ── Recommendation (never an action) ────────────────────────────────────────
KEEP_V1 = "KEEP_V1"
ELIGIBLE_FOR_V2_REVIEW = "ELIGIBLE_FOR_V2_REVIEW"
CUSTOM_WRAPPER_REVIEW = "CUSTOM_WRAPPER_REVIEW"
BLOCKED = "BLOCKED"

# ── Per-value forms ─────────────────────────────────────────────────────────
FORM_JSON_PER_LINE = "json_per_line"
FORM_TOKENS = "tokens"
FORM_JSON = "json"
FORM_COMMA = "comma"
FORM_OTHER = "other"
FORM_EMPTY = "empty"

#: Languages the generic harnesses wrap. C and C++ are self-contained: the
#: learner writes a whole program, so their template needs main(), not a
#: Solution class. Kept here rather than imported so this module stays pure.
REFLECTION_LANGUAGES = ("python", "java", "javascript", "js")
SELF_CONTAINED_LANGUAGES = ("c", "cpp", "c++")


def _lines(text):
    return [line for line in str(text).split("\n") if line.strip()]


def classify_stdin(text):
    """
    The input form of one test case.

    `json_per_line` is what v1's Python and JavaScript harnesses require;
    `tokens` is what v2 and Java's harness read. A value can be both — "5" is
    a valid JSON number AND a single token — which is why AMBIGUOUS exists
    rather than a forced choice.
    """
    if not str(text).strip():
        return FORM_EMPTY
    lines = _lines(text)
    if not lines:
        return FORM_EMPTY

    all_json = True
    any_non_json = False
    for line in lines:
        try:
            json.loads(line)
        except Exception:
            all_json = False
            any_non_json = True

    if all_json:
        # Every line parses as JSON. Whether it is ALSO token-readable depends
        # on whether the values are scalars, which classify_case resolves.
        return FORM_JSON_PER_LINE
    if any_non_json and all(line.split() for line in lines):
        return FORM_TOKENS
    return FORM_OTHER


def classify_output(text):
    """The output form of one test case."""
    value = str(text).strip()
    if not value:
        return FORM_EMPTY
    if value[0] in "[{":
        return FORM_JSON
    if "," in value and " " not in value:
        return FORM_COMMA
    return FORM_TOKENS


def _json_is_scalar_per_line(text):
    """True when every line is a JSON SCALAR — the case both harnesses read."""
    for line in _lines(text):
        try:
            parsed = json.loads(line)
        except Exception:
            return False
        if isinstance(parsed, (list, dict)):
            return False
    return True


def classify_case(case, index):
    """
    (verdict, detail) for one test case, using only its stored values.

    Never returns the values themselves — this feeds a report that gets
    archived, and stdin/expected_output are grading truth.
    """
    if not isinstance(case, dict):
        return INVALID_TESTS, f"case {index + 1} is not an object"
    if "stdin" not in case or "expected_output" not in case:
        return INVALID_TESTS, f"case {index + 1} is missing stdin/expected_output"
    if not isinstance(case.get("stdin"), str) or not isinstance(
        case.get("expected_output"), str
    ):
        return INVALID_TESTS, f"case {index + 1} has a non-string stdin/expected_output"

    stdin_form = classify_stdin(case["stdin"])
    output_form = classify_output(case["expected_output"])

    if stdin_form == FORM_EMPTY:
        return INVALID_TESTS, f"case {index + 1} has empty stdin"
    if stdin_form == FORM_OTHER or output_form in (FORM_COMMA, FORM_OTHER):
        # A convention neither generic harness implements — seed_problems.py's
        # comma-separated questions are the known example, and they carry a
        # custom wrapper precisely because of it.
        return NEITHER, f"case {index + 1} uses a non-generic convention"

    v1_ok = stdin_form == FORM_JSON_PER_LINE
    v2_ok = (
        stdin_form == FORM_TOKENS
        or (stdin_form == FORM_JSON_PER_LINE and _json_is_scalar_per_line(case["stdin"]))
    ) and output_form == FORM_TOKENS

    if v1_ok and v2_ok:
        return AMBIGUOUS_CONTRACT, ""
    if v2_ok:
        return V2_ONLY, ""
    if v1_ok:
        return V1_ONLY, ""
    return NEITHER, f"case {index + 1} fits neither generic harness"


def classify_boilerplate(boilerplate):
    """
    (status, per_language, problems) from structure alone.

    Structural, not compiled — compilation belongs to P2.7b. It still catches
    the defect the audit proved: the generator emits `class Solution` for C++
    and a bare function for C, but both are SELF-CONTAINED and run raw, so a
    template with no main() cannot link.
    """
    if not isinstance(boilerplate, dict) or not boilerplate:
        return MISSING_BOILERPLATE, {}, ["no boilerplate for any language"]

    per_language = {}
    problems = []
    for language, template in boilerplate.items():
        key = str(language).lower()
        if not isinstance(template, str) or not template.strip():
            per_language[key] = False
            problems.append(f"{key}: empty template")
            continue

        if key in SELF_CONTAINED_LANGUAGES:
            ok = "main" in template
            if not ok:
                problems.append(
                    f"{key}: self-contained language with no main() — cannot link"
                )
        elif key in REFLECTION_LANGUAGES:
            ok = "Solution" in template
            if not ok:
                problems.append(f"{key}: reflection harness needs a Solution class")
        else:
            ok = True  # unknown language: not this command's call to make

        per_language[key] = ok

    status = INVALID_BOILERPLATE if problems else None
    return status, per_language, problems


def classify_question(question, has_active_reference=None):
    """
    The full Tier-A record for one question.

    `has_active_reference` is passed in rather than queried so this stays pure
    and the caller controls the query count. None means "not determined".
    """
    cases = question.hidden_test_cases
    boilerplate = question.boilerplate_code
    custom = question.hidden_wrapper_code
    configured = (getattr(question, "execution_contract_version", "") or "v1").strip()

    record = {
        "id": question.id,
        "title": question.title,
        "configured_contract": configured,
        "implied_contract": None,
        "hidden_test_count": len(cases) if isinstance(cases, list) else 0,
        "languages_present": sorted(boilerplate) if isinstance(boilerplate, dict) else [],
        "languages_structurally_valid": {},
        "has_custom_wrapper": bool(isinstance(custom, dict) and custom),
        "custom_wrapper_languages": sorted(custom) if isinstance(custom, dict) and custom else [],
        "internally_consistent": None,
        "has_active_reference": has_active_reference,
        # Tier B: needs an approved reference and a working Judge0.
        "oracle_agrees": "UNKNOWN",
        "oracle_deterministic": "UNKNOWN",
        "currently_gradable": None,
        "recommendation": None,
        "blockers": [],
    }

    boiler_status, per_language, boiler_problems = classify_boilerplate(boilerplate)
    record["languages_structurally_valid"] = per_language
    if boiler_status == MISSING_BOILERPLATE:
        record["blockers"].append(MISSING_BOILERPLATE)
    elif boiler_status == INVALID_BOILERPLATE:
        record["blockers"].append(INVALID_BOILERPLATE)
    record["boilerplate_problems"] = boiler_problems

    if not isinstance(cases, list) or not cases:
        record["implied_contract"] = MISSING_TESTS
        record["internally_consistent"] = None
        record["currently_gradable"] = False
        record["blockers"].append(MISSING_TESTS)
        record["recommendation"] = BLOCKED
        return record

    verdicts, details = [], []
    for index, case in enumerate(cases):
        verdict, detail = classify_case(case, index)
        verdicts.append(verdict)
        if detail:
            details.append(detail)
    record["case_detail"] = details

    if INVALID_TESTS in verdicts:
        record["implied_contract"] = INVALID_TESTS
        record["internally_consistent"] = False
        record["currently_gradable"] = False
        record["blockers"].append(INVALID_TESTS)
        record["recommendation"] = BLOCKED
        return record

    concrete = {v for v in verdicts if v != AMBIGUOUS_CONTRACT}
    if len(concrete) > 1:
        # The cases disagree. Guessing which format is "the real one" is
        # exactly the silent decision this command exists to avoid.
        record["implied_contract"] = CONTRACT_MISMATCH
        record["internally_consistent"] = False
        record["currently_gradable"] = False
        record["blockers"].append(CONTRACT_MISMATCH)
        record["recommendation"] = BLOCKED
        return record

    implied = concrete.pop() if concrete else AMBIGUOUS_CONTRACT
    record["internally_consistent"] = True

    if record["has_custom_wrapper"]:
        # A per-question wrapper defines its own I/O and outranks the version
        # in _build_executable, so the generic classification does not apply.
        record["implied_contract"] = CUSTOM_WRAPPER
        record["currently_gradable"] = None   # needs the wrapper to be executed
        record["recommendation"] = CUSTOM_WRAPPER_REVIEW
        return record

    record["implied_contract"] = implied

    if implied == NEITHER:
        record["currently_gradable"] = False
        record["blockers"].append(NEITHER)
        record["recommendation"] = BLOCKED
        return record

    gradable = (
        (implied == V1_ONLY and configured == "v1")
        or (implied == V2_ONLY and configured == "v2")
        or implied == AMBIGUOUS_CONTRACT
    )
    record["currently_gradable"] = gradable

    if not gradable:
        record["blockers"].append(
            f"data implies {implied} but the question is configured {configured}"
        )

    if implied == V2_ONLY and configured == "v1":
        # The state the audit proved: correct solutions cannot pass.
        record["recommendation"] = ELIGIBLE_FOR_V2_REVIEW
    elif implied == AMBIGUOUS_CONTRACT:
        record["recommendation"] = ELIGIBLE_FOR_V2_REVIEW
    elif implied == V1_ONLY:
        record["recommendation"] = KEEP_V1
    else:
        record["recommendation"] = KEEP_V1 if gradable else BLOCKED

    if record["blockers"] and record["recommendation"] != CUSTOM_WRAPPER_REVIEW:
        # Boilerplate problems do not change which contract the DATA implies,
        # but they do stop the question being publishable.
        if MISSING_BOILERPLATE in record["blockers"] or INVALID_BOILERPLATE in record["blockers"]:
            record["recommendation"] = BLOCKED

    return record
