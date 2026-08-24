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


def contract_blockers(question, *, pre_image_record=None, using=None):
    """
    `stub_blockers`, plus the conditions specific to CHOOSING A CONTRACT
    (M2 P2.7h-27) — [] means the contract may be set.

    ── The exact inverse of `signature_blockers` on one clause ──────────────

    `signature_blockers` refuses a starter that ALREADY declares parameters:
    its job is to declare a signature where none exists. This refuses a starter
    that declares NONE. The contract is a decision ABOUT a signature, so it can
    only be taken once one exists, and the two commands therefore cover
    disjoint states of the same question by construction. A question can never
    be eligible for both at the same moment.

    That ordering is the whole safety argument for the stage. v3 rewrites the
    stdin a stored expected output was authored against, so choosing the
    contract after cases exist would silently reinterpret every one of them.
    `stub_blockers` already requires `hidden_test_cases == []`, which pins the
    decision to the window between `declare_signature` and suite authoring.
    """
    blockers = stub_blockers(question, using=using)

    # Same anchor as `signature_blockers`, and for the same reason: the
    # statement write removes the live marker, so eligibility is bound to what
    # the batch froze rather than to what the row says now.
    captured = (pre_image_record.content if pre_image_record is not None
                else question.content)
    if Question.PLACEHOLDER_MARKER not in (captured or ""):
        blockers.append(
            "this question was not a placeholder stub when the batch was "
            "frozen; a contract may only be set on a reseed candidate")

    source = (question.boilerplate_code or {}).get("python") or ""
    if not source.strip():
        blockers.append(
            "the question has no python starter, so there is no signature to "
            "choose a contract for")
        return blockers

    if execution_adapter.accepts_variable_arity(source):
        blockers.append(
            "the starter is still variadic (*args/**kwargs); declare_signature "
            "has not run and there is no arity to decide a contract from")
    if execution_adapter.has_keyword_only_parameters(source):
        blockers.append(
            "the starter declares keyword-only parameters, whose names stdin "
            "cannot supply under any contract this harness implements")

    declared = execution_adapter.declared_signature(source)
    if declared is None:
        blockers.append("the starter declares nothing the harness could call")
    elif not declared[1]:
        blockers.append(
            "the starter declares no parameters, so no contract distinction "
            "applies to it")

    return blockers


def contract_target(question):
    """
    (version, verdict) for a question whose signature is declared.

    The rule is IMPORTED from the census, never restated. Phase 11 tested it
    against the real adapter and pinned it with a mutation sweep; a second
    copy here would be a second thing to keep true.

    A per-question wrapper defines its own I/O contract and is consulted by
    `_build_executable` BEFORE the version is, so the generic harness never
    runs and the splat defect it fixes cannot occur. Such a question keeps v1
    — migrating it would change a field that nothing reads.
    """
    from groups import execution_contract, reseed_contract_census as census

    source = (question.boilerplate_code or {}).get("python") or ""

    if census.has_custom_wrapper(getattr(question, "hidden_wrapper_code", None)):
        return execution_contract.CONTRACT_V1, census.V1_SUFFICIENT

    verdict = census.v3_requirement(source)
    if verdict == census.V3_REQUIRED:
        return execution_contract.CONTRACT_V3, verdict
    if verdict == census.V1_SUFFICIENT:
        return execution_contract.CONTRACT_V1, verdict
    return None, census.UNKNOWN


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

    # ── the contract stage (M2 P2.7h-27) ────────────────────────────────
    #
    # Unlike the other two, this stage has NO reliable live signal. A question
    # whose signature is V1_SUFFICIENT keeps `execution_contract_version` at
    # "v1", which is byte-identical to a question nobody has looked at yet.
    # "Decided v1" and "never decided" are therefore indistinguishable from
    # the row alone, and only the append-only trail separates them — which is
    # exactly why the command records an action even when it writes no field.
    contract_recorded = \
        RemediationAction.CLASS_CONTRACT_DECLARATION in classes
    if contract_recorded and signature_present:
        intended, _verdict = contract_target(question)
        live = question.execution_contract_version or "v1"
        if intended is not None and live != intended:
            discrepancies.append(
                f"a CONTRACT_DECLARATION was recorded and the signature "
                f"implies {intended}, but the question declares {live} — the "
                f"write was reverted or overwritten after it was audited")
    if contract_recorded and not signature_present:
        discrepancies.append(
            "a CONTRACT_DECLARATION was recorded but the starter declares no "
            "signature; the contract was chosen for a shape that is gone")

    statement_done = marker_gone and statement_recorded
    signature_done = signature_present and signature_recorded
    contract_done = signature_done and contract_recorded

    if contract_done and statement_done:
        stage = ReseedLedger.STAGE_CONTRACT
    elif statement_done and signature_done:
        stage = ReseedLedger.STAGE_SIGNATURE
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
