"""
Eligibility for the v1 -> v2 structural migration (Phase 1 M14).

PURE. Every function here takes a question's captured state and returns a
verdict. Nothing touches the ORM, nothing executes, nothing writes --
`test_multilingual_gate` asserts that at the AST level. The command
`migrate_contract_v2` does the reading and the one write.

WHY THE SAME FUNCTIONS RUN TWICE
    The command calls `eligibility` once to build the plan an operator reads,
    and again on the LOCKED row inside the transaction immediately before the
    write. Both calls go through this module, so the plan and the write cannot
    disagree about what "safe" means. A check that existed only in the plan
    would let the row change underneath it; a check that existed only in the
    transaction would show the operator a plan that was never going to hold.

WHY THIS MODULE DOES NOT DECIDE "SAFE"
    It asks `migration_readiness.classify` -- the same function behind
    `v2_migration_worklist`, tested and mutation-tested in its own right -- and
    refuses anything it does not call SAFE_TO_MIGRATE. Re-deriving that verdict
    here would give the platform two definitions of "safe to migrate" that are
    free to drift, which is how a worklist says one thing and a migration does
    another.

    What this module adds is only what the classifier cannot know: that a
    migration must start from v1, and that a question carrying its own wrapper
    would ignore the contract column entirely.
"""

import types

from groups import execution_contract, migration_readiness, pre_image
from groups.question_artifact import _frame, digest_of

#: The one column the migration changes.
MIGRATED_FIELD = "execution_contract_version"

#: Where a migration must start, and the only place it may go.
SOURCE_CONTRACT = execution_contract.CONTRACT_V1
TARGET_CONTRACT = execution_contract.CONTRACT_V2

#: The starter whose signature declares the contract; the adapter reads Python.
CONTRACT_LANGUAGE = "python"

#: Recorded on every audit row this migration produces.
REASON = "M14_STRUCTURAL_MIGRATION"


def effective_version(question_id, state):
    """The contract the question grades under today, or None if unknown.

    Delegates to `execution_contract.contract_version` rather than re-stating
    its rule, so "blank means v1" has exactly one definition. An unknown
    declaration returns None and is refused by the caller -- never guessed.
    """
    stand_in = types.SimpleNamespace(
        pk=question_id,
        execution_contract_version=state.get(MIGRATED_FIELD))
    try:
        return execution_contract.contract_version(stand_in)
    except execution_contract.UnknownExecutionContract:
        return None


def eligibility(question_id, state):
    """(classification, [refusal, ...]) for migrating one question to v2.

    `classification` is the classifier's verdict, or None when the question is
    not structural at all. An empty refusal list is the ONLY thing that
    permits the write.
    """
    refusals = []

    declared = effective_version(question_id, state)
    if declared is None:
        refusals.append(
            f"declares an unknown execution contract "
            f"{state.get(MIGRATED_FIELD)!r}; refusing to migrate from a "
            f"contract nobody can name")
    elif declared != SOURCE_CONTRACT:
        refusals.append(
            f"grades under {declared}, not {SOURCE_CONTRACT}. This migration "
            f"only moves a question FROM {SOURCE_CONTRACT}; anything else is "
            f"either already done or a different contract with its own review")

    if state.get("hidden_wrapper_code"):
        refusals.append(
            f"carries its own wrapper ({sorted(state['hidden_wrapper_code'])}), "
            f"which takes precedence over the contract column -- declaring "
            f"{TARGET_CONTRACT} would change nothing while claiming to migrate")

    starter = (state.get("boilerplate_code") or {}).get(CONTRACT_LANGUAGE) or ""
    verdict = migration_readiness.classify(
        question_id, starter, state.get("hidden_test_cases") or [],
        version=state.get(MIGRATED_FIELD) or "")

    if verdict is None:
        refusals.append(
            "is not a structural question: the classifier finds no TreeNode or "
            "ListNode anywhere in its starter, so there is nothing for the v2 "
            "structural adapter to build")
    elif not verdict.safe:
        refusals.append(
            f"the authoritative classifier says {verdict.bucket}, not "
            f"{migration_readiness.SAFE_TO_MIGRATE}: {verdict.reason}")

    return verdict, refusals


# ── staleness ─────────────────────────────────────────────────────────────

def suite_changes(captured_cases, live_cases):
    """Which half of a suite moved since the freeze, for the refusal message.

    Diagnostic only. The staleness GATE is `pre_image.differing_fields`, which
    compares the whole suite; this says which part of it moved so the
    operator knows whether an answer changed or a question did -- the
    distinction the audit trail is built around.
    """
    before = pre_image.suite_case_identities(captured_cases)
    after = pre_image.suite_case_identities(live_cases)
    moved = []
    if len(before) != len(after):
        moved.append(f"case count {len(before)} -> {len(after)}")
    if [i for _, i, _ in before] != [i for _, i, _ in after]:
        moved.append("stored inputs")
    if [e for _, _, e in before] != [e for _, _, e in after]:
        moved.append("expected outputs")
    return moved or ["case fields other than input and expected output"]


def staleness(record, question):
    """[refusal, ...] when the live row no longer matches its frozen pre-image.

    The pre-image is what a rollback restores. If the row has moved since it
    was frozen, the rollback anchor no longer describes the question being
    migrated, and a restore would silently discard whatever changed it.
    """
    if record is None:
        # Nothing to compare against is itself a refusal, not a crash. The
        # command's gate never passes None here, but a function that decides
        # whether a rollback anchor is usable should say so plainly rather
        # than depend on every caller having checked first.
        return ["there is no pre-image to compare the question against; a "
                "migration without one has nothing to roll back to"]
    differing = pre_image.differing_fields(record, question)
    if not differing:
        return []
    detail = []
    for name in differing:
        if name == "hidden_test_cases":
            detail.append(
                f"hidden_test_cases ({', '.join(suite_changes(record.captured_state()['hidden_test_cases'], question.hidden_test_cases))})")
        else:
            detail.append(name)
    return [f"the question has changed since its pre-image was frozen: "
            f"{'; '.join(detail)}. Re-capture and re-freeze before migrating; "
            f"a stale pre-image is not a rollback anchor"]


# ── digests, for the plan and the audit row ───────────────────────────────

def _digest(label, value):
    """One component, in the system's single canonical encoding."""
    return digest_of([_frame("m14_component", label), _frame(label, value)])


def component_digests(state):
    """Digests an operator and an auditor can compare, one per component."""
    identities = pre_image.suite_case_identities(state.get("hidden_test_cases"))
    return {
        "content": _digest("content", state.get("content")),
        "boilerplate_code": _digest("boilerplate_code",
                                    state.get("boilerplate_code")),
        "hidden_test_cases": _digest("hidden_test_cases",
                                     state.get("hidden_test_cases")),
        "inputs": _digest("inputs", [i for _, i, _ in identities]),
        "expected_outputs": _digest("expected_outputs",
                                    [e for _, _, e in identities]),
    }


def audit_detail(record, verdict, digests):
    """The `RemediationAction.detail` text for one migration.

    The row's own columns already carry the question, batch, pre-image,
    operator, timestamp and post-digest. This carries what they cannot: why,
    from where, to where, on whose verdict, and the component digests at the
    moment of the write -- so a later reader can tell exactly which answer key
    began being read under v2.
    """
    fields = [
        ("reason", REASON),
        ("from", SOURCE_CONTRACT),
        ("to", TARGET_CONTRACT),
        ("classifier", verdict.bucket),
        ("parameter_kinds", ",".join(verdict.parameter_kinds) or "-"),
        ("return_kind", verdict.return_kind or "scalar"),
        ("pre_image_id", record.pk),
        ("pre_image_digest", record.state_digest),
    ]
    fields += [(f"{name}_digest", value) for name, value in digests.items()]
    return "; ".join(f"{key}={value}" for key, value in fields)
