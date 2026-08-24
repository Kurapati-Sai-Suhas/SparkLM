"""
Eligibility and validation for the reseed authoring path (M2 P2.7h-18).

ONE definition of "virgin stub", used by `reseed_statement`, by
`declare_signature` and by the orchestrator's dry-run. Three copies of a
precondition are three chances for them to disagree, and the one that
disagrees quietly is the one that writes to a published question.

── Why a state precondition rather than a diff gate ────────────────────────

`remediate_boilerplate` refuses added, renamed or reordered parameters because
a starter's signature is load-bearing for hidden tests that ALREADY EXIST —
`execution_adapter` binds every stored case against the declared signature, so
moving the signature under a live suite silently changes what the answers
mean.

A reseed candidate has no such suite. It has a placeholder statement, an empty
`hidden_test_cases`, no oracle execution, no approval, and DRAFT/UNVERIFIED
trust. Nothing is bound to `*args, **kwargs`, so replacing it cannot corrupt
grading truth — there is none yet.

So the safe distinction is the QUESTION'S STATE, not the diff's shape. That is
what `stub_blockers` computes, and every published question fails it on
several independent grounds rather than one.
"""

import ast

from groups import execution_adapter
from groups.models import (
    OracleExecution, Question, QuestionApproval, RemediationAction,
)

#: A body that is allowed to remain in an authored starter. Anything else and
#: the "starter" is carrying logic — which is how a reference solution ends up
#: shipped to the learner who was asked to write it.
_TRIVIAL_BODY = (ast.Pass, ast.Expr)


def stub_blockers(question, *, using=None):
    """
    The conditions BOTH authoring stages require — [] means eligible.

    Every check is independent and all of them are evaluated, so a refusal
    reports every reason rather than the first. A published question fails on
    several grounds at once; a bug in any one of them does not open the door.

    The placeholder marker is deliberately NOT checked here. See
    `statement_blockers` and `signature_blockers`, which disagree about WHEN
    the marker has to have been present, for a reason that matters.
    """
    using = using or question._state.db
    blockers = []

    if question.status != Question.STATUS_DRAFT:
        blockers.append(f"status is {question.status}, not DRAFT")
    if question.trust_state != Question.TRUST_UNVERIFIED:
        blockers.append(f"trust_state is {question.trust_state}, not UNVERIFIED")
    if question.hidden_test_cases:
        blockers.append(
            f"{len(question.hidden_test_cases)} hidden test case(s) already "
            f"exist; authoring would change what they are testing")
    if QuestionApproval.objects.using(using).filter(question=question).exists():
        blockers.append("a QuestionApproval already exists")
    if OracleExecution.objects.using(using).filter(question=question).exists():
        blockers.append("an OracleExecution already exists")

    return blockers


def statement_blockers(question, *, using=None):
    """
    `stub_blockers`, plus a LIVE placeholder marker.

    Statement generation runs first and authors over the placeholder, so the
    marker must still be there when it runs. This is the exact complement of
    `remediate_statement`, which refuses a question that still carries one.
    """
    blockers = stub_blockers(question, using=using)
    if Question.PLACEHOLDER_MARKER not in (question.content or ""):
        blockers.append(
            "the statement carries no placeholder marker — this question has "
            "a real statement and is not a reseed candidate")
    return blockers


def signature_blockers(question, *, pre_image_record=None, using=None):
    """
    `stub_blockers`, plus the two conditions specific to the starter.

    ── Why the marker is checked against the PRE-IMAGE, not live content ────

    The pipeline authors the statement first, and that write removes the
    placeholder marker. Requiring a LIVE marker here would make signature
    declaration impossible on exactly the questions it exists for: the second
    stage could never run after the first.

    So the question that has to be answered is not "is this a stub now" but
    "was this a stub when the batch was frozen" — and the pre-image is the
    frozen, verified, immutable record of precisely that. Anchoring
    eligibility to it is strictly stronger than checking live content:
    it binds this authority to one frozen batch, so a question that was
    already a real question when the slice opened can never acquire a
    declared signature through this command, no matter what happens to its
    statement in between.

    Without a pre-image the command has no reversible ground to stand on and
    `require_pre_image` refuses first; the live fallback below exists only so
    that the dry-run can explain an ineligible question rather than crash.
    """
    blockers = stub_blockers(question, using=using)

    captured = (pre_image_record.content if pre_image_record is not None
                else question.content)
    if Question.PLACEHOLDER_MARKER not in (captured or ""):
        blockers.append(
            "this question was not a placeholder stub when the batch was "
            "frozen; a signature may only be declared on a reseed candidate")

    source = (question.boilerplate_code or {}).get("python") or ""
    if source:
        declared = execution_adapter.declared_signature(source)
        variadic = execution_adapter.accepts_variable_arity(source)
        if declared is not None and declared[1] and not variadic:
            blockers.append(
                f"the starter already declares parameters {declared[1]!r}; "
                f"this command declares a signature where none exists and "
                f"will not redeclare one")
    return blockers


def validate_signature(current_source, proposed_source, *, language="python"):
    """
    Refusals for a proposed starter — [] means it may be written.

    Checks the SHAPE of what is being declared. It deliberately does not look
    at hidden tests or expected outputs, because at this point in the
    lifecycle there are none; binding against real cases is
    `binding_blockers`, called later by whoever writes the suite.
    """
    if language != "python":
        return [f"signature declaration supports python only, not {language!r}"]

    refusals = []

    try:
        tree = ast.parse(proposed_source)
    except SyntaxError as exc:
        return [f"the proposed starter is not valid Python: {exc.msg} "
                f"(line {exc.lineno})"]
    try:
        compile(proposed_source, "<declared-starter>", "exec")
    except (SyntaxError, ValueError) as exc:
        return [f"the proposed starter does not compile: {exc}"]

    # ── class and method identity must not move ──────────────────────
    current_classes = _class_names(current_source)
    proposed_classes = _class_names(proposed_source)
    if current_classes and proposed_classes != current_classes:
        refusals.append(
            f"the class was renamed: {current_classes} -> {proposed_classes}. "
            f"A declaration names the signature, not the class.")

    methods = execution_adapter.public_method_names(proposed_source)
    if len(methods) != 1:
        refusals.append(
            f"expected exactly one public method, found {len(methods)}: "
            f"{methods}. The wrapper picks between public methods, so two is "
            f"an ambiguity the harness would have to guess at.")

    current_methods = execution_adapter.public_method_names(current_source)
    if current_methods and methods and current_methods != methods:
        refusals.append(
            f"the method was renamed: {current_methods} -> {methods}. The "
            f"statement and every future test refer to it by name.")

    declared = execution_adapter.declared_signature(proposed_source)
    if declared is None:
        return refusals + ["the proposed starter declares nothing callable"]

    _name, parameters = declared

    # ── the signature itself ─────────────────────────────────────────
    if execution_adapter.accepts_variable_arity(proposed_source):
        refusals.append(
            "the proposed starter still takes *args or **kwargs. Declaring a "
            "signature means replacing variadics with named parameters.")
    if execution_adapter.has_keyword_only_parameters(proposed_source):
        refusals.append(
            "the proposed starter has keyword-only parameters. Their names "
            "would have to come from stdin, which the adapter cannot supply.")
    if not parameters:
        refusals.append(
            "the proposed starter declares no parameters. A problem the "
            "learner cannot be given input for is not a problem.")

    names = [name for name, _annotation in parameters]
    if len(set(names)) != len(names):
        refusals.append(f"duplicate parameter names: {names}")

    for name, annotation in parameters:
        if not annotation:
            refusals.append(
                f"parameter {name!r} has no type annotation. The adapter "
                f"coerces stdin by annotation; an undeclared parameter is "
                f"coerced by guesswork.")
        elif execution_adapter.classify_annotation(annotation) == \
                execution_adapter.UNDECLARED:
            refusals.append(
                f"parameter {name!r} is annotated {annotation!r}, which the "
                f"adapter cannot classify into a coercion kind.")

    # ── the body must stay a stub ────────────────────────────────────
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for statement in node.body:
                if not isinstance(statement, _TRIVIAL_BODY):
                    refusals.append(
                        f"the body of {node.name!r} is not a stub. A starter "
                        f"carrying logic is how a solution reaches the learner "
                        f"who was asked to write it.")
                    break

    return refusals


def binding_blockers(source, cases):
    """
    Whether a declared signature can actually bind the cases it will be
    graded against.

    Not called during authoring — no cases exist yet. It is the handshake the
    hidden-test phase runs once it has written a suite, and it lives here so
    the two halves of the contract are defined in one place.
    """
    blockers = []
    for index, case in enumerate(cases or [], start=1):
        stdin = (case or {}).get("stdin", "")
        invocation = execution_adapter.build_invocation(stdin, source)
        if not invocation.ok:
            blockers.append(
                f"case {index} does not bind: {invocation.outcome}")
    return blockers


def derive_stage(question, batch, *, using=None):
    """
    The TRUE stage, from live state and the audit trail — never the ledger.

    This is why the ledger carries no digest. It says which stage to attempt;
    what actually happened is re-read every time from the two records that
    cannot be written by the coordinator: the question itself, and the
    append-only action trail.

    Returns (stage, discrepancies). A discrepancy means live state and the
    audit trail disagree — someone or something else edited the question —
    and the caller must refuse rather than advance.
    """
    from groups.models import ReseedLedger

    using = using or question._state.db
    classes = set(
        RemediationAction.objects.using(using)
        .filter(batch=batch, question=question)
        .values_list("action_class", flat=True))

    marker_gone = Question.PLACEHOLDER_MARKER not in (question.content or "")
    statement_recorded = RemediationAction.CLASS_STATEMENT_GENERATION in classes

    source = (question.boilerplate_code or {}).get("python") or ""
    declared = execution_adapter.declared_signature(source) if source else None
    signature_present = bool(
        declared and declared[1]
        and not execution_adapter.accepts_variable_arity(source))
    signature_recorded = \
        RemediationAction.CLASS_SIGNATURE_DECLARATION in classes

    discrepancies = []
    if marker_gone and not statement_recorded:
        discrepancies.append(
            "the placeholder is gone but no STATEMENT_GENERATION was recorded "
            "in this batch — the statement was changed by something else")
    if statement_recorded and not marker_gone:
        discrepancies.append(
            "a STATEMENT_GENERATION was recorded but the placeholder is still "
            "present — the write was reverted after it was audited")
    if signature_present and not signature_recorded:
        discrepancies.append(
            "a signature is declared but no SIGNATURE_DECLARATION was "
            "recorded in this batch")
    if signature_recorded and not signature_present:
        discrepancies.append(
            "a SIGNATURE_DECLARATION was recorded but the starter declares no "
            "signature — the write was reverted after it was audited")

    statement_done = marker_gone and statement_recorded
    signature_done = signature_present and signature_recorded

    if statement_done and signature_done:
        stage = ReseedLedger.STAGE_COMPLETE
    elif statement_done:
        stage = ReseedLedger.STAGE_STATEMENT
    else:
        stage = ReseedLedger.STAGE_PENDING
    return stage, discrepancies


def _class_names(source):
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return []
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
