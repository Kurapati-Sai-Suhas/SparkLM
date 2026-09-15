"""
Which questions can move from the v1 to the v2 execution contract (M14).

READ-ONLY BY CONSTRUCTION. Nothing here touches the ORM: every function takes
a starter source and stored test cases and returns a verdict. The command
`v2_migration_worklist` does the reading. `test_multilingual_gate` asserts at
the AST level that this module never reaches `.objects`, never calls `.save()`
and never assigns a trust field.

WHY THIS EXISTS AS CODE RATHER THAN A DOCUMENT
    M12 produced its worklist by inspection and wrote the result into
    `docs/V2_MIGRATION_WAVE.md` -- 22 SAFE_TO_MIGRATE, of which the document
    enumerates 16. Nothing implemented the classification, so the number could
    not be re-derived, checked, or corrected as content changed underneath it.
    Regenerating it found four defect classes in three iterations (32 -> 19 ->
    29 -> 25 -> 28), none of which a prose list could have surfaced.

    This module's output decides which questions are eligible to have their
    input representation changed. A false SAFE silently corrupts an answer
    key. It is safety-critical, so it is tested, and the tests are mutation-
    tested.

WHAT "SAFE" MEANS HERE, AND WHAT IT DOES NOT
    SAFE_TO_MIGRATE means only: no *statically detectable* blocker. It is a
    precondition for entering the wave, never a substitute for it. Execution
    parity across Python/JavaScript/Java, an operator-authored reference and
    a Judge0 Oracle run still decide correctness. Nothing here executes
    anything.
"""

import ast
import json
import re

from groups import content_quarantine, execution_adapter, execution_contract
from groups import language_readiness, structural_types

# ── buckets ──────────────────────────────────────────────────────────────

SAFE_TO_MIGRATE = "SAFE_TO_MIGRATE"
BLOCKED_BY_STARTER = "BLOCKED_BY_STARTER"
BLOCKED_BY_TEST_CONTRACT_INPUT = "BLOCKED_BY_TEST_CONTRACT_INPUT"
BLOCKED_BY_TEST_CONTRACT_ARITY = "BLOCKED_BY_TEST_CONTRACT_ARITY"
BLOCKED_BY_TEST_CONTRACT_OUTPUT = "BLOCKED_BY_TEST_CONTRACT_OUTPUT"
UNSUPPORTED_COLLECTION = "UNSUPPORTED_COLLECTION"
UNSUPPORTED_NODE = "UNSUPPORTED_NODE"
UNSUPPORTED_IMMUTABLE = "UNSUPPORTED_IMMUTABLE"
STATEFUL_STRUCTURE = "STATEFUL_STRUCTURE"
NOT_IN_GRADED_SIGNATURE = "NOT_IN_GRADED_SIGNATURE"
QUARANTINED = "QUARANTINED"

#: Reported in this order; also the order a question is tested against them.
BUCKETS = (
    SAFE_TO_MIGRATE,
    BLOCKED_BY_TEST_CONTRACT_INPUT,
    BLOCKED_BY_TEST_CONTRACT_ARITY,
    BLOCKED_BY_TEST_CONTRACT_OUTPUT,
    BLOCKED_BY_STARTER,
    UNSUPPORTED_COLLECTION,
    UNSUPPORTED_NODE,
    UNSUPPORTED_IMMUTABLE,
    STATEFUL_STRUCTURE,
    NOT_IN_GRADED_SIGNATURE,
    QUARANTINED,
)

#: Kinds the v2 harness builds from a canonical array.
STRUCTURAL_KINDS = frozenset({structural_types.TREE,
                              structural_types.LINKED_LIST})

_REGISTRY_NAMES = frozenset(s.name for s in structural_types.REGISTRY)

#: `list[TreeNode]`, `List[ListNode]`, `tuple[TreeNode, int]` -- N structures.
_COLLECTION = re.compile(r"\b(list|sequence|tuple|set|iterable)\s*\[", re.I)


class Classification:
    """One question's verdict, with everything needed to act on it."""

    def __init__(self, question_id, bucket, reason, *, method="",
                 parameter_kinds=(), return_kind="", notes=(),
                 version="", readiness=None):
        self.question_id = question_id
        self.bucket = bucket
        self.reason = reason
        self.method = method
        self.parameter_kinds = list(parameter_kinds)
        self.return_kind = return_kind
        self.notes = list(notes)
        self.version = version
        self.readiness = dict(readiness or {})

    @property
    def safe(self):
        return self.bucket == SAFE_TO_MIGRATE

    def as_dict(self):
        return {
            "question_id": self.question_id,
            "bucket": self.bucket,
            "reason": self.reason,
            "method": self.method,
            "parameter_kinds": self.parameter_kinds,
            "return_kind": self.return_kind or "scalar",
            "notes": self.notes,
            "execution_contract_version": self.version,
            "language_readiness": self.readiness,
        }


# ── signature reading ────────────────────────────────────────────────────

def graded_signature_annotations(source):
    """(parameter annotations, return annotation) of the GRADED method.

    `language_readiness._assess_python` deliberately reads EVERY annotation in
    the file, because any of them raises NameError at definition time. That is
    the right question for readiness and the wrong one for migration, which
    asks instead: what must the harness BIND and SERIALISE? Only the graded
    method's own signature answers that.

    Returns (None, None) when no graded method can be identified.
    """
    node, _is_method = execution_adapter.chosen_function(source or "")
    if node is None:
        return None, None
    params = [execution_adapter._annotation_text(argument.annotation)
              for argument in node.args.args if argument.annotation]
    returns = (execution_adapter._annotation_text(node.returns)
               if node.returns else "")
    return params, returns


def constructor_annotations(source):
    """Annotations on the SOLUTION class's `__init__` only.

    A structure reaching the Solution constructor is a stateful-object design
    problem -- `Solution(head)` then `getRandom()` -- not a function whose
    argument is a structure. The v2 adapter binds arguments to the graded
    call and has no contract for constructing the Solution itself, so these
    must never enter the wave. q382 "Linked List Random Node" is the case in
    the bank.

    Restricted to the Solution class deliberately. Reading every `__init__`
    in the file instead catches `TreeNode.__init__(self, val, left:
    'TreeNode')` -- which EVERY structural starter defines -- and reports an
    ordinary helper class as a stateful problem. `Solution` is chosen the
    same way `execution_adapter.chosen_function` chooses it (M12), so the two
    cannot disagree about which class is the solution.
    """
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return []
    classes = [node for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef)]
    if not classes:
        return []
    target = next((node for node in classes if node.name == "Solution"),
                  classes[0])
    out = []
    for node in target.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            out.extend(execution_adapter._annotation_text(a.annotation)
                       for a in node.args.args if a.annotation)
    return out


def all_annotation_names(source):
    """Every identifier in any annotation, or None when the starter is broken."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        annotations = [a.annotation for a in node.args.args if a.annotation]
        if node.returns:
            annotations.append(node.returns)
        for annotation in annotations:
            names |= language_readiness.annotation_names(annotation)
    return names


def declares_collection_of_structures(annotation):
    """True when a structural name sits inside a collection subscript.

    `structural_types.by_annotation` matches whole words anywhere in the text.
    That is correct for the three spellings in the bank -- `Optional[TreeNode]`,
    `'TreeNode'`, `TreeNode | None` -- and wrong for `list[TreeNode]`, which is
    N trees. q95 returns five of them and stores an array of arrays, which any
    "is it a JSON array?" test happily accepts. The v2 serialiser renders one
    structure; there is no general contract for a collection of them.
    """
    if not annotation:
        return False
    return bool(_COLLECTION.search(annotation)
                and structural_types.by_annotation(annotation))


# ── canonical representation ─────────────────────────────────────────────

def parse_arguments(stdin):
    """Stored stdin split into positional arguments.

    No stripping. An EMPTY argument is how the canonical contract spells the
    empty structure -- the leading-empty-argument case M16 fixed in the Java
    harness, where `trim()` was deleting it -- so `"\\n[0]"` is two arguments,
    the first an empty list, and must never collapse to one.
    """
    return (stdin or "").split("\n")


def canonical_round_trip(text, kind):
    """(is_fixed_point, canonical_text) for a stored structural value.

    `canonical_text` is None when the value does not build a structure at all,
    which is the only case that is always fatal.
    """
    text = (text or "").strip()
    if text == "":
        return True, text
    try:
        values = json.loads(text)
    except ValueError:
        return False, None
    if not isinstance(values, list):
        return False, None
    try:
        if kind == structural_types.TREE:
            rebuilt = structural_types.serialize_tree(
                structural_types.build_tree(values))
        else:
            rebuilt = structural_types.serialize_linked_list(
                structural_types.build_linked_list(values))
    except Exception:                                      # noqa: BLE001
        return False, None
    return _compact(text) == _compact(rebuilt), _compact(rebuilt)


def _compact(value):
    """Whitespace-insensitive JSON text, for comparing two spellings."""
    if not isinstance(value, str):
        return json.dumps(value, separators=(",", ":"))
    try:
        return json.dumps(json.loads(value), separators=(",", ":"))
    except ValueError:
        return value.strip()


# ── the classifier ───────────────────────────────────────────────────────

def classify(question_id, python_source, test_cases, *, version="",
             readiness=None):
    """Bucket one question. Pure: no database, no execution, no network."""
    source = python_source or ""
    common = {"version": version, "readiness": readiness}

    if not source.strip():
        return None

    names = all_annotation_names(source)
    if names is None:
        return None                       # unparseable: not a migration case

    unsupported = sorted(names & set(structural_types.UNSUPPORTED))
    buildable = sorted(names & _REGISTRY_NAMES)
    if not unsupported and not buildable:
        return None                       # not a structural question at all

    # 1. Structures with no adapter, whatever they are annotating.
    if unsupported:
        bucket = (UNSUPPORTED_IMMUTABLE if "ImmutableListNode" in unsupported
                  else UNSUPPORTED_NODE)
        return Classification(
            question_id, bucket,
            f"declares {', '.join(unsupported)}: "
            f"{structural_types.UNSUPPORTED[unsupported[0]]}", **common)

    # 2. Quarantine outranks every structural verdict below it. Placed here so
    #    no later branch can reach a SAFE conclusion for a held question: a
    #    content-trust hold is about whether the stored answer is TRUE, which
    #    no amount of representational tidiness settles.
    blocker = content_quarantine.blocker_for(question_id)
    if blocker:
        return Classification(question_id, QUARANTINED, blocker, **common)

    params, returns = graded_signature_annotations(source)
    if params is None:
        return Classification(question_id, BLOCKED_BY_STARTER,
                              "no graded method could be identified", **common)

    method_node, _ = execution_adapter.chosen_function(source)
    method = getattr(method_node, "name", "")
    common["method"] = method

    in_signature = (any(structural_types.by_annotation(a) for a in params)
                    or bool(structural_types.by_annotation(returns)))

    # 3. The structure never crosses the graded boundary.
    if not in_signature:
        if any(structural_types.by_annotation(a)
               for a in constructor_annotations(source)):
            return Classification(
                question_id, STATEFUL_STRUCTURE,
                "the structure is constructed into the Solution object, not "
                "passed to the graded method; the v2 adapter binds arguments "
                "to the call and has no contract for this", **common)
        return Classification(
            question_id, NOT_IN_GRADED_SIGNATURE,
            "a structural type is named in the starter but never appears in "
            "the graded signature", **common)

    # 4. Collections of structures.
    collections = [a for a in list(params) + [returns]
                   if declares_collection_of_structures(a)]
    if collections:
        return Classification(
            question_id, UNSUPPORTED_COLLECTION,
            f"declares {collections[0]!r}: N structures, and the v2 harness "
            f"builds and serialises exactly one", **common)

    # 5. Would the starter even be READY under v2?
    verdict = language_readiness.assess_source(
        source, "python", version=execution_contract.CONTRACT_V2)
    if verdict.verdict == language_readiness.NOT_READY:
        return Classification(question_id, BLOCKED_BY_STARTER,
                              f"{verdict.cause}: {verdict.reason}", **common)

    kinds = execution_contract.v2_parameter_kinds(source)
    return_kind = execution_contract.v2_return_kind(source)
    common.update(parameter_kinds=kinds, return_kind=return_kind)

    if not test_cases:
        return Classification(question_id, BLOCKED_BY_STARTER,
                              "no hidden test cases stored", **common)

    notes = []
    for index, case in enumerate(test_cases):
        arguments = parse_arguments(case.get("stdin"))

        # 6. Arity, and the three different things a mismatch can mean.
        if len(arguments) > len(kinds):
            surplus = arguments[len(kinds):]
            if any(a.strip() for a in surplus):
                return Classification(
                    question_id, BLOCKED_BY_TEST_CONTRACT_ARITY,
                    f"case {index} carries {len(arguments)} arguments against "
                    f"arity {len(kinds)}, and the surplus {surplus!r} is not "
                    f"empty: a real value is being dropped", notes=notes,
                    **common)
            notes.append(f"case {index}: trailing-newline storage artifact")
        elif len(arguments) < len(kinds):
            return Classification(
                question_id, BLOCKED_BY_TEST_CONTRACT_ARITY,
                f"case {index} carries only {len(arguments)} argument(s); the "
                f"graded signature declares {len(kinds)}", notes=notes,
                **common)

        # 7. Inputs. Asymmetric with outputs BY DESIGN, see below.
        for position, kind in enumerate(kinds):
            if kind not in STRUCTURAL_KINDS:
                continue
            value = arguments[position] if position < len(arguments) else ""
            fixed, canonical = canonical_round_trip(value, kind)
            if canonical is None:
                return Classification(
                    question_id, BLOCKED_BY_TEST_CONTRACT_INPUT,
                    f"case {index} argument {position} ({kind}) does not "
                    f"build a structure: {value[:48]!r}", notes=notes, **common)
            if not fixed:
                # An input is fed through build_tree/build_linked_list, which
                # normalises it, so `[4,2,6,1,3,null,null]` and `[4,2,6,1,3]`
                # produce an identical tree. Cosmetic, not a blocker.
                notes.append(
                    f"case {index} arg {position}: non-canonical spelling "
                    f"{value[:28]!r} normalises to {canonical[:28]!r}")

        # 8. Outputs. An output is compared as TEXT and gets no parser
        #    normalisation, so a non-canonical spelling is a real failure:
        #    q450 stores `[5,3,6,2,4,null]` where the canonical serialiser
        #    emits `[5,3,6,2,4]`, and migrating it would break an answer key
        #    that passes today.
        if return_kind in STRUCTURAL_KINDS:
            expected = case.get("expected_output")
            fixed, canonical = canonical_round_trip(expected, return_kind)
            if canonical is None:
                return Classification(
                    question_id, BLOCKED_BY_TEST_CONTRACT_OUTPUT,
                    f"case {index} expected output is not a canonical "
                    f"structure: {(expected or '')[:48]!r}", notes=notes,
                    **common)
            if not fixed:
                return Classification(
                    question_id, BLOCKED_BY_TEST_CONTRACT_OUTPUT,
                    f"case {index} expected output {(expected or '')[:32]!r} "
                    f"canonicalises to a DIFFERENT text {canonical[:32]!r}; "
                    f"the stored answer key would stop matching", notes=notes,
                    **common)

    return Classification(question_id, SAFE_TO_MIGRATE,
                          "no statically detectable blocker", notes=notes,
                          **common)
