"""
Blast-radius analysis for the v1 execution contract (M2 P2.7).

PURE. Takes plain dicts, returns plain dicts. No ORM, no I/O — the command that
feeds it does the reading, so this module can be exercised exhaustively on
synthetic input without a database and cannot itself touch production.

── What it measures ────────────────────────────────────────────────────────

For each question: what v1 actually delivers to the learner's function, versus
what that function's own signature says it expects. A disagreement is not a
style question — it means the code is called with an argument of the wrong
type, and whatever it returns is graded as if it were the answer.

The severe class is `text_retyped`: the signature declares a string and v1
hands over an int, a float, a bool or None. A question about leading zeros,
digit counting or character positions cannot be answered correctly from an
integer, so its stored expected outputs were produced by code that was asked
the wrong question.

── What it deliberately does NOT do ────────────────────────────────────────

It reads. It does not execute, does not fix, and does not decide. Counting how
many questions are exposed is a prerequisite for a repair decision, not the
decision, and no repair is authorised in this phase.
"""

import ast
import json
import random

from groups import execution_adapter

#: `**parsed_input` — the argument NAMES come from the input, so arity and type
#: cannot be modelled from the stdin alone.
KWARGS = "kwargs"


# ── What v1 actually does ───────────────────────────────────────────────────

def v1_call_arguments(stdin):
    """
    The argument types `GENERIC_PYTHON_WRAPPER` passes, in call order.

    Reproduces the wrapper's four branches exactly, because each is a different
    way to get the call wrong:

        whole-blob JSON is a list  ->  SPLATTED: [1,2] calls solve(1, 2)
        whole-blob JSON is a dict  ->  **kwargs, named by the input
        whole-blob JSON otherwise  ->  one argument of that type
        per-line JSON, ALL lines   ->  one argument per line
        anything else              ->  one argument, the raw string

    The per-line branch is all-or-nothing: one unparseable line sends the WHOLE
    input through as a single string, so "hello\\n7" is one argument, not two.

    Empty input yields `[]` — zero arguments. That is not a quirk of this
    analysis; it is what the wrapper does, and it is why a question whose
    function takes a parameter cannot be called at all on blank stdin.
    """
    stripped = (stdin or "").strip()
    try:
        parsed = json.loads(stripped)
    except Exception:
        lines = [line for line in stripped.split("\n") if line.strip() != ""]
        try:
            return [type(json.loads(line)) for line in lines]
        except Exception:
            return [str]

    if isinstance(parsed, list):
        return [type(item) for item in parsed]
    if isinstance(parsed, dict):
        return KWARGS
    return [type(parsed)]


def v1_delivers(stdin):
    """The single argument type v1 passes, or a list when it passes several."""
    arguments = v1_call_arguments(stdin)
    if arguments == KWARGS:
        return KWARGS
    return arguments[0] if len(arguments) == 1 else arguments


def v1_retypes_to_non_text(stdin):
    """Whether any argument v1 passes is something other than `str`."""
    arguments = v1_call_arguments(stdin)
    if arguments == KWARGS:
        return False
    return any(argument is not str for argument in arguments)


def positional_text_mismatch(parameters, arguments):
    """
    Whether a parameter DECLARED as text is handed a non-text value.

    Positional, deliberately. "any parameter is text and any argument is not"
    overcounts badly: `wordBreak(s: str, wordDict: list[str])` is called
    correctly with a string and a list, and the coarse test flags it because a
    list is not a string. The claim being made is about one parameter and the
    value that reaches IT.

    `zip` stops at the shorter sequence; a length disagreement is a different
    finding (`arity_mismatch`) and is reported as one.
    """
    return any(
        declares_text(annotation) and delivered is not str
        for (_name, annotation), delivered in zip(parameters, arguments)
    )


# ── What the question declares ──────────────────────────────────────────────

# Signature reading lives in `execution_adapter` and is imported, not copied.
# Two parsers that agree today are two parsers that disagree after the next
# edit, and this module's whole job is to describe what the adapter will do.
chosen_function = execution_adapter.chosen_function
declared_signature = execution_adapter.declared_signature
accepts_variable_arity = execution_adapter.accepts_variable_arity
public_method_names = execution_adapter.public_method_names


def declares_text(annotation_text):
    """Whether an annotation asks for text, per the adapter's own table."""
    return execution_adapter.classify_annotation(annotation_text) == \
        execution_adapter.TEXT


# ── Per-question classification ─────────────────────────────────────────────

def classify(question):
    """
    One question's exposure. `question` is a dict with keys: id, status,
    trust_state, execution_contract_version, boilerplate_code,
    hidden_test_cases, hidden_wrapper_code.

    Returns a dict of findings. Findings are independent, not a ranking: a
    question can be both text-retyped and boolean-mismatched, and counting it
    once under the "worst" heading would understate the repair surface.
    """
    findings = {
        "id": question.get("id"),
        "contract": question.get("execution_contract_version") or "v1",
        "status": question.get("status"),
        "trust_state": question.get("trust_state"),
        "analysable": False,
        "reasons": [],
    }

    wrappers = question.get("hidden_wrapper_code") or {}
    if isinstance(wrappers, dict) and any(
            isinstance(value, str) and value.strip() for value in wrappers.values()):
        # A per-question wrapper defines its own I/O contract and is checked
        # first by `_build_executable`, so the generic harness never runs.
        findings["reasons"].append("custom_wrapper")
        return findings

    boilerplate = question.get("boilerplate_code") or {}
    source = boilerplate.get("python") if isinstance(boilerplate, dict) else None
    if not (isinstance(source, str) and source.strip()):
        findings["reasons"].append("no_python_starter")
        return findings

    signature = declared_signature(source)
    if signature is None:
        findings["reasons"].append("no_callable_in_starter")
        return findings

    _name, parameters = signature
    findings["analysable"] = True
    findings["parameter_count"] = len(parameters)

    methods = public_method_names(source)
    if len(methods) > 1:
        # v1 calls dir(sol)[0]. Alphabetical, not intended.
        findings["reasons"].append("ambiguous_entry_point")
        findings["public_methods"] = methods

    variadic = accepts_variable_arity(source)
    if variadic:
        # `*args`/`**kwargs` — a placeholder starter with no declared contract.
        # No arity claim can be made, so none is made.
        findings["reasons"].append("variadic_starter")

    annotated = [text for _n, text in parameters if text]
    if not annotated:
        findings["reasons"].append("unannotated")

    text_parameters = [name for name, text in parameters if declares_text(text)]
    findings["text_parameters"] = text_parameters

    cases = question.get("hidden_test_cases") or []
    if not isinstance(cases, list):
        findings["reasons"].append("malformed_test_cases")
        return findings

    findings["case_count"] = len(cases)
    _classify_cases(findings, cases, parameters, variadic)
    return findings


def _classify_cases(findings, cases, parameters, variadic):
    delivered_shapes = set()
    blank_inputs = 0
    retyped_cases = []
    arity_mismatch_cases = []
    non_string_fields = []
    boolean_mismatch = 0
    expects = len(parameters)

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            findings["reasons"].append("malformed_test_cases")
            continue

        raw_stdin = case.get("stdin")
        raw_expected = case.get("expected_output")

        # `GradingService.grade` calls `.strip()` on both without checking.
        # A list here does not grade wrongly — it raises AttributeError inside
        # the grader, so the submission 500s and the learner sees nothing.
        # Counted, not repaired: it is a distinct failure from a wrong answer.
        if not isinstance(raw_stdin, (str, type(None))):
            non_string_fields.append(("stdin", index + 1))
            continue
        if not isinstance(raw_expected, (str, type(None))):
            non_string_fields.append(("expected_output", index + 1))
            continue

        stdin = (raw_stdin or "").replace("\\n", "\n")
        if not stdin.strip():
            blank_inputs += 1

        arguments = v1_call_arguments(stdin)
        if arguments == KWARGS:
            # `**parsed_input` names its own arguments; arity and type depend
            # on keys this analysis cannot check against the signature.
            delivered_shapes.add(KWARGS)
            continue

        delivered_shapes.add(tuple(item.__name__ for item in arguments))

        if not variadic and len(arguments) != expects:
            # The call raises TypeError before the learner's code runs, so this
            # question fails EVERY submission, correct ones included.
            arity_mismatch_cases.append(index + 1)

        if positional_text_mismatch(parameters, arguments):
            retyped_cases.append(index + 1)

        if (raw_expected or "").strip() in ("True", "False"):
            boolean_mismatch += 1

    if non_string_fields:
        findings["reasons"].append("non_string_test_field")
        findings["non_string_fields"] = [
            f"{field}@case{position}" for field, position in non_string_fields]

    if blank_inputs:
        findings["reasons"].append("blank_stdin")
        findings["blank_input_cases"] = blank_inputs

    if arity_mismatch_cases:
        findings["reasons"].append("arity_mismatch")
        findings["arity_mismatch_cases"] = arity_mismatch_cases
        findings["declared_arity"] = expects

    if len(delivered_shapes) > 1:
        # The same function is called with different argument types depending
        # on which test case runs. At most one of those can be the contract.
        findings["reasons"].append("type_unstable")
        findings["delivered_shapes"] = sorted(
            str(shape) for shape in delivered_shapes)

    if retyped_cases:
        findings["reasons"].append("text_retyped")
        findings["retyped_cases"] = retyped_cases

    if boolean_mismatch:
        findings["reasons"].append("boolean_casing")
        findings["boolean_casing_cases"] = boolean_mismatch

    if expects == 0:
        findings["reasons"].append("zero_parameters")


# ── Aggregation ─────────────────────────────────────────────────────────────

#: Reported in this order — severity descending, so the summary reads as a
#: priority list rather than an alphabetical one.
REASON_ORDER = (
    "non_string_test_field",
    "arity_mismatch",
    "text_retyped",
    "type_unstable",
    "ambiguous_entry_point",
    "boolean_casing",
    "blank_stdin",
    "zero_parameters",
    "variadic_starter",
    "unannotated",
    "malformed_test_cases",
    "custom_wrapper",
    "no_python_starter",
    "no_callable_in_starter",
)


def summarise(classifications):
    """
    Counts per reason, plus the totals a decision actually needs.

    `reason_counts` is over questions that HAVE hidden test cases, because that
    is the only set where a percentage means anything: a question with no tests
    is never executed, so it can be neither miscalled nor correct. Counting the
    1,140-odd placeholder starters would put a large number next to
    `variadic_starter` and invite the reader to divide it by a denominator it
    does not belong to. `reason_counts_all` keeps the full tally for anyone who
    wants it, clearly separated.
    """
    def tally(findings):
        counts = {reason: 0 for reason in REASON_ORDER}
        for finding in findings:
            for reason in set(finding["reasons"]):
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    analysable = [f for f in classifications if f["analysable"]]
    # The denominator that matters. A question with no hidden test cases is
    # never executed, so it cannot be miscalled — counting it as "clean" would
    # dilute every ratio below with questions that were never graded at all.
    graded = [f for f in analysable if f.get("case_count", 0) > 0]
    counts = tally(graded)
    return {
        "total_questions": len(classifications),
        "analysable": len(analysable),
        "not_analysable": len(classifications) - len(analysable),
        "with_test_cases": len(graded),
        "without_test_cases": len(classifications) - len(graded),
        "reason_counts": counts,
        "reason_counts_all": tally(classifications),
        # The numbers a reseed decision actually turns on.
        #
        # `provably_miscalled` — the signature says text, v1 passes a number,
        # bool or None. Whatever the stored expected output is, it answers a
        # different question from the one that was asked.
        #
        # `never_callable` — v1 passes the wrong NUMBER of arguments, so the
        # call raises TypeError before the learner's code runs. Every
        # submission fails, including correct ones.
        #
        # `grader_crashes` — `stdin` or `expected_output` is not a string, and
        # `GradingService.grade` calls `.strip()` on both without checking.
        # The submission raises inside the grader rather than returning a
        # verdict at all.
        "provably_miscalled": counts.get("text_retyped", 0),
        "never_callable": counts.get("arity_mismatch", 0),
        "grader_crashes": counts.get("non_string_test_field", 0),
    }


# ── Classification under canonical execution ────────────────────────────────

SAFE = "SAFE"
NEEDS_MIGRATION = "NEEDS_MIGRATION"

#: Exclusive verdicts, most severe first. Unlike `reason_counts` — where a
#: question is counted under every finding it matches — each question gets
#: exactly ONE verdict, because "what do we do with it" has one answer.
VERDICT_ORDER = (
    execution_adapter.INVALID_INPUT,
    execution_adapter.CONTRACT_MISMATCH,
    execution_adapter.NEEDS_MANUAL_REVIEW,
    NEEDS_MIGRATION,
    SAFE,
)


def v1_call_values(stdin):
    """
    The argument VALUES v1 passes today, not just their types.

    Needed to answer "would adopting the canonical envelope change this
    question's behaviour?" — which is the difference between SAFE and
    NEEDS_MIGRATION, and the only honest basis for saying a migration is a
    no-op for a given question.
    """
    stripped = (stdin or "").strip()
    try:
        parsed = json.loads(stripped)
    except Exception:
        lines = [line for line in stripped.split("\n") if line.strip() != ""]
        try:
            return [json.loads(line) for line in lines]
        except Exception:
            # v1 passes the STRIPPED blob, not the raw one.
            return [stripped]
    if isinstance(parsed, list):
        return list(parsed)
    if isinstance(parsed, dict):
        return KWARGS
    return [parsed]


def canonical_verdict(question):
    """
    What canonical execution would make of one question. Reads only.

    Nothing here repairs anything: a SAFE verdict means the adapter can invoke
    the question as its signature declares, NOT that its stored expected
    outputs are correct. Those remain unprovenanced until an oracle run and a
    human say otherwise.
    """
    wrappers = question.get("hidden_wrapper_code") or {}
    if isinstance(wrappers, dict) and any(
            isinstance(v, str) and v.strip() for v in wrappers.values()):
        return execution_adapter.NEEDS_MANUAL_REVIEW, "custom wrapper"

    boilerplate = question.get("boilerplate_code") or {}
    source = boilerplate.get("python") if isinstance(boilerplate, dict) else None
    if not (isinstance(source, str) and source.strip()):
        return execution_adapter.NEEDS_MANUAL_REVIEW, "no python starter"

    cases = question.get("hidden_test_cases") or []
    if not isinstance(cases, list) or not cases:
        return execution_adapter.NEEDS_MANUAL_REVIEW, "no hidden test cases"

    if len(public_method_names(source)) > 1:
        return (execution_adapter.NEEDS_MANUAL_REVIEW,
                "more than one public method, so the entry point is ambiguous")

    would_change = False
    for position, case in enumerate(cases, start=1):
        stdin, expected, problem = execution_adapter.read_test_case(case)
        if problem:
            return execution_adapter.INVALID_INPUT, f"case {position}: {problem}"

        invocation = execution_adapter.build_invocation(stdin, source)
        if not invocation.ok:
            return invocation.outcome, f"case {position}: {invocation.detail}"
        if invocation.warnings:
            return (execution_adapter.NEEDS_MANUAL_REVIEW,
                    f"case {position}: {', '.join(invocation.warnings)}")

        if not execution_adapter.is_canonical_output(expected):
            return (NEEDS_MIGRATION,
                    f"case {position}: expected output {expected.strip()!r} is "
                    f"not canonical and can never be produced")

        if v1_call_values(stdin.replace("\\n", "\n")) != invocation.arguments:
            would_change = True

    if would_change:
        return (NEEDS_MIGRATION,
                "canonical execution passes different arguments from v1, so "
                "the stored expected outputs must be re-derived")
    return SAFE, "v1 already invokes this question as its signature declares"


def summarise_verdicts(questions):
    """{verdict: count} plus one example id per verdict."""
    counts = {verdict: 0 for verdict in VERDICT_ORDER}
    examples = {}
    for question in questions:
        verdict, detail = canonical_verdict(question)
        counts[verdict] = counts.get(verdict, 0) + 1
        examples.setdefault(verdict, {"id": question.get("id"), "why": detail})
    return {"counts": counts, "examples": examples}


# ── Sampling ────────────────────────────────────────────────────────────────

#: The stratum for a question with test cases and no finding against it. Named
#: rather than omitted: a sample of only-broken questions cannot tell you
#: whether the clean ones are actually clean.
CLEAN = "clean"


def stratify(classifications):
    """
    {stratum: [question ids]}, each list sorted ascending.

    A question appears in EVERY stratum it matches. Assigning it to one
    "primary" defect would make the sample depend on an ordering choice, and a
    reviewer looking at the boolean-casing stratum would never see the case
    that is also arity-broken.
    """
    strata = {}
    for finding in classifications:
        if not finding["analysable"] or not finding.get("case_count", 0):
            continue
        reasons = [r for r in finding["reasons"] if r in REASON_ORDER]
        for reason in reasons or [CLEAN]:
            strata.setdefault(reason, []).append(finding["id"])
    return {name: sorted(ids) for name, ids in strata.items()}


def stratified_sample(classifications, size, seed, exclude=()):
    """
    `size` question ids covering as many strata as possible, reproducibly.

    Round-robin across strata rather than proportional allocation: the purpose
    is to put a human in front of at least one example of each defect class,
    and proportional allocation would spend nearly every slot on the two
    largest classes and show none of the rare ones.

    Deterministic given `seed`: strata are visited in `REASON_ORDER`, ids are
    sorted before shuffling, and the shuffle uses a local `Random`. Re-running
    with the same seed selects the same questions, so a second reviewer audits
    the same sample rather than a new one.
    """
    excluded = set(exclude)
    rng = random.Random(seed)

    pools = {}
    for name, ids in stratify(classifications).items():
        available = [i for i in ids if i not in excluded]
        rng.shuffle(available)
        pools[name] = available

    order = [name for name in REASON_ORDER if name in pools]
    if CLEAN in pools:
        order.append(CLEAN)

    selected, seen = [], set()
    while len(selected) < size and any(pools[name] for name in order):
        for name in order:
            if len(selected) >= size:
                break
            while pools[name]:
                candidate = pools[name].pop()
                if candidate not in seen:
                    seen.add(candidate)
                    selected.append({"id": candidate, "stratum": name})
                    break
    return selected
