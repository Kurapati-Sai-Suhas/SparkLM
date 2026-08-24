"""
Contract binding rules and the reseed contract census (M2 P2.7h-26).

Local/synthetic only. No Judge0 call, no production read, no write.

These tests pin the rules the Phase 11 census depends on. The census counts
questions; if the rules underneath it are wrong the count is worthless, so the
rules are asserted against the real `execution_adapter` and the real
`GradingService` seam rather than described in prose.

q1974 is the regression fixture: a one-parameter method whose argument is a
list, which contract v1 cannot invoke because its harness splats a JSON array
positionally.
"""

import ast

import pytest

from groups import execution_adapter as ea
from groups import execution_contract
from groups import reseed_contract_census as census
from groups.models import CodingPortal, Question, Topic
from groups.services import ExecutionContractError, GradingService

VARIADIC = ("class Solution:\n"
            "    def findGreatestCommonDivisorOfArray(self, *args, **kwargs):\n"
            "        pass\n")
SINGLE_CONTAINER = ("class Solution:\n"
                    "    def findGreatestCommonDivisorOfArray(self, "
                    "nums: list[int]) -> int:\n        pass\n")
TWO_SCALARS = ("class Solution:\n"
               "    def widgetCount(self, s: str, k: int) -> int:\n"
               "        pass\n")
SINGLE_SCALAR = ("class Solution:\n"
                 "    def countOnes(self, colors: str) -> bool:\n"
                 "        pass\n")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Census Portal")
    row, _ = Topic.objects.get_or_create(
        name="CensusTopic", defaults={"structure_type": "flat",
                                      "portal": portal})
    return row


def question(topic, starter, contract="v1", question_id=9920):
    return Question.objects.create(
        id=question_id, title="T", topic=topic, base_difficulty=1300.0,
        content="<p>x</p>", boilerplate_code={"python": starter},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version=contract)


# ═════════════════════════════════════════════════════════════
# 11C — the binding rules, asserted against the code
# ═════════════════════════════════════════════════════════════

def test_v1_is_the_default_for_a_blank_contract():
    """A question written before versioning must keep grading as it did."""
    blank = Question(execution_contract_version="")
    assert execution_contract.contract_version(blank) == \
        execution_contract.CONTRACT_V1


def test_an_unknown_contract_is_refused_rather_than_defaulted():
    unknown = Question(id=1, execution_contract_version="v9")
    with pytest.raises(execution_contract.UnknownExecutionContract):
        execution_contract.contract_version(unknown)


@pytest.mark.django_db
def test_v1_passes_stdin_through_unchanged(topic):
    """
    v1's only transformation is literal-\\n expansion. The wrapper does the
    interpreting, which is precisely why it can guess wrong.
    """
    row = question(topic, TWO_SCALARS)
    assert GradingService.prepare_stdin(row, "python", '"abc"\n2') == '"abc"\n2'
    assert GradingService.prepare_stdin(row, "python", 'a\\nb') == "a\nb"


@pytest.mark.django_db
def test_v3_builds_a_canonical_envelope_from_the_declared_signature(topic):
    """
    v3 introduces no new harness. It hands the SAME wrapper a JSON array built
    server-side, so the splat becomes correct by construction.
    """
    row = question(topic, SINGLE_CONTAINER, contract="v3")
    assert GradingService.prepare_stdin(row, "python", "[3, 6, 4]") == "[[3,6,4]]"


@pytest.mark.django_db
def test_v3_is_python_only(topic):
    row = question(topic, SINGLE_CONTAINER, contract="v3")
    with pytest.raises(ExecutionContractError, match="python only"):
        GradingService.prepare_stdin(row, "java", "[1]")


# ═════════════════════════════════════════════════════════════
# 11E — the q1974 regression, in memory
# ═════════════════════════════════════════════════════════════

def test_v1_splats_a_single_list_into_separate_arguments():
    """
    The defect, isolated from Judge0. The generic v1 harness json-parses the
    whole blob and, seeing a list, calls `method(*parsed)`. A one-parameter
    method therefore receives len(list) arguments.
    """
    from groups.services import GENERIC_PYTHON_WRAPPER

    assert "target_method(*args)" in GENERIC_PYTHON_WRAPPER or \
        "target_method(*parsed_input)" in GENERIC_PYTHON_WRAPPER, \
        "the v1 harness no longer splats; this test's premise has changed"

    parsed = __import__("json").loads("[3, 6, 4]")
    assert isinstance(parsed, list)
    declared = ea.declared_signature(SINGLE_CONTAINER)
    assert len(declared[1]) == 1
    # one declared parameter, three positional arguments -> TypeError at run time
    assert len(parsed) != len(declared[1])


@pytest.mark.django_db
def test_v3_binds_the_same_signature_correctly(topic):
    """The fix, and the reason it is a fix: one argument, not three."""
    invocation = ea.build_invocation("[3, 6, 4]", SINGLE_CONTAINER)

    assert invocation.ok
    assert invocation.arguments == [[3, 6, 4]]
    assert invocation.envelope() == "[[3,6,4]]"


def test_the_variadic_placeholder_refuses_v3_binding():
    """
    The stored starter of every reseed candidate declares no arity, so v3
    cannot be applied before `declare_signature` runs. This is the guard that
    stops a premature contract migration.
    """
    invocation = ea.build_invocation("[3, 6, 4]", VARIADIC)

    assert not invocation.ok
    assert invocation.outcome == ea.NEEDS_MANUAL_REVIEW
    assert "*args" in invocation.detail


@pytest.mark.django_db
def test_the_stored_starter_and_the_artifact_starter_differ(topic):
    """
    Until the signature is declared, the question stores the variadic stub.
    Any contract decision taken from the STORED starter is a decision about a
    signature that does not exist yet.
    """
    row = question(topic, VARIADIC)

    stored = ea.declared_signature(row.boilerplate_code["python"])
    artifact = ea.declared_signature(SINGLE_CONTAINER)

    assert stored[1] == []          # no arity declared
    assert len(artifact[1]) == 1    # the eventual signature
    with pytest.raises(ExecutionContractError, match="no arity is declared"):
        GradingService.prepare_stdin(
            question(topic, VARIADIC, contract="v3", question_id=9921),
            "python", "[3, 6, 4]")


# ═════════════════════════════════════════════════════════════
# 11I — shape classification, the census's load-bearing logic
# ═════════════════════════════════════════════════════════════

REQUIRED = census.V3_REQUIRED
SUFFICIENT = census.V1_SUFFICIENT
UNKNOWN = census.UNKNOWN


@pytest.mark.parametrize("source,expected", [
    (SINGLE_CONTAINER, REQUIRED),    # list[int]      -> splat breaks it
    (TWO_SCALARS, SUFFICIENT),       # str, int       -> splat is correct
    (SINGLE_SCALAR, SUFFICIENT),     # str            -> whole blob is the field
    ("class S:\n    def f(self, a: list[int], b: list[int]) -> int:\n"
     "        pass\n", SUFFICIENT),  # two containers -> splat is correct
    ("class S:\n    def f(self, a: list[int], b: int) -> int:\n"
     "        pass\n", SUFFICIENT),  # mixed          -> splat is correct
    ("class S:\n    def f(self, d: dict[str, int]) -> int:\n"
     "        pass\n", REQUIRED),    # single mapping -> splat breaks it
])
def test_the_v3_requirement_rule(source, expected):
    assert census.v3_requirement(source) == expected


@pytest.mark.parametrize("source", [
    VARIADIC,
    "class S:\n    def f(self, *, k: int) -> int:\n        pass\n",
    "class S:\n    def f(self) -> int:\n        pass\n",
])
def test_an_undeclared_arity_is_unknown_not_sufficient(source):
    """
    The distinction the census turns on. A variadic stub is UNKNOWN, not
    "does not need v3" — answering V1_SUFFICIENT would silently claim all
    1,140 candidates are fine on the contract they carry.
    """
    assert census.v3_requirement(source) == UNKNOWN


def test_a_lone_unannotated_parameter_is_unknown_not_sufficient():
    """
    The case that makes the rule three-valued rather than boolean, and the one
    a naive reading gets wrong.

    Arity is known — one parameter — so it is tempting to conclude the splat
    is safe. It is not: the whole rule turns on whether that parameter's value
    is a CONTAINER, and an unannotated parameter says nothing about its kind.
    `f(a)` fed `[3, 6, 4]` is exactly the q1974 defect if `a` is a list.

    Getting this wrong is not academic. Reading it as V1_SUFFICIENT moved 208
    real questions into the wrong column of the reference class and shifted
    the projected v3 population by four points.
    """
    assert census.v3_requirement(
        "class S:\n    def f(self, a):\n        pass\n") == UNKNOWN


@pytest.mark.parametrize("source", [
    # one ordinary parameter PLUS *args — arity looks like 1 and is not
    "class S:\n    def f(self, a: list[int], *args) -> int:\n        pass\n",
    # one ordinary parameter PLUS a keyword-only extra stdin cannot name
    "class S:\n    def f(self, a: list[int], *, k: int) -> int:\n"
    "        pass\n",
    "class S:\n    def f(self, a: str, **kwargs) -> int:\n        pass\n",
])
def test_a_partly_declared_signature_is_unknown(source):
    """
    The survivors of the first mutation sweep, and the reason the variadic and
    keyword-only guards are not redundant with the empty-signature check.

    For a PURE `*args` stub `declared_signature` returns no parameters at all,
    so the empty check already answers UNKNOWN and removing the guards changes
    nothing visible. Add one real parameter alongside the `*args` and the two
    diverge: the signature now reports exactly one parameter of container
    kind, which reads as V3_REQUIRED — a confident verdict about a function
    that may in fact take four arguments.
    """
    assert census.v3_requirement(source) == UNKNOWN


@pytest.mark.parametrize("source", [{"python": SINGLE_CONTAINER}, 17, [],
                                    object()])
def test_a_non_string_starter_is_refused_not_crashed(source):
    """
    `boilerplate_code` is a JSON field, so its "python" entry is whatever was
    stored there. The adapter parses with `ast.parse`, which raises TypeError
    — not SyntaxError — on a non-string, and a census that dies on one bad row
    reports nothing about the other 1,139.
    """
    assert census.v3_requirement(source) == UNKNOWN
    assert census.classify_shape(source) == "A"


def test_several_unannotated_parameters_are_sufficient_not_unknown():
    """
    The complement, and the reason the fix is not just "return UNKNOWN when
    annotations are missing". With two parameters the splat delivers two
    arguments whatever the kinds are, so the arity is right and v3 has
    nothing to correct. Over-reporting UNKNOWN hides real answers.
    """
    assert census.v3_requirement(
        "class S:\n    def f(self, a, b):\n        pass\n") == SUFFICIENT


def test_a_custom_wrapper_is_not_a_v3_candidate():
    """
    `_build_executable` checks the per-question wrapper FIRST, so the generic
    harness never runs and the splat cannot happen. Reporting such a question
    as needing v3 would schedule a migration that fixes nothing.
    """
    record = census.classify({
        "id": 1, "execution_contract_version": "v1",
        "boilerplate_code": {"python": SINGLE_CONTAINER},
        "hidden_wrapper_code": {"python": "{user_code}\nprint('custom')"}})

    assert record["harness"] == census.CUSTOM_HARNESS
    assert record["v3_requirement"] == SUFFICIENT


@pytest.mark.parametrize("blank", [None, {}, {"python": "   "}, "not a dict"])
def test_a_blank_wrapper_entry_does_not_count_as_custom(blank):
    """A stray "" must not silently exempt a question from the census."""
    assert census.has_custom_wrapper(blank) is False


# ── shape classes ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("expected,source", [
    ("A", None),
    ("A", "   "),
    ("B", "class S:\n    def f(self, a: int) ->\n"),
    ("C", "SPEED = 3\n"),
    ("D", "class S:\n    def a(self, x: int): pass\n"
          "    def b(self, y: int): pass\n"),
    ("E", VARIADIC),
    ("F", "class S:\n    def f(self, *, k: int): pass\n"),
    ("G", "class S:\n    def f(self): pass\n"),
    ("H", "class S:\n    def f(self, a, b): pass\n"),
    ("I", SINGLE_CONTAINER),
    ("J", SINGLE_SCALAR),
    ("K", TWO_SCALARS),
])
def test_every_shape_class_is_reachable(expected, source):
    assert census.classify_shape(source) == expected


def test_the_shape_classes_are_ordered_most_blocking_first():
    """
    First match wins, so a starter that is BOTH ambiguous and variadic must
    report the ambiguity — the harness picks a method before it ever looks at
    that method's arity. Counting it under E would describe the wrong repair.
    """
    both = ("class S:\n"
            "    def alpha(self, *args, **kwargs): pass\n"
            "    def beta(self, n: int): pass\n")
    assert census.classify_shape(both) == "D"


def test_unparseable_and_no_callable_are_not_merged():
    """
    `chosen_function` returns None for both, so the census re-parses to tell
    them apart. They need different repairs: one is a broken file, the other
    is a file with nothing to call.
    """
    assert census.classify_shape("def (:\n") == "B"
    assert census.classify_shape("VALUE = 1\n") == "C"


def test_every_class_letter_has_a_name_and_a_description():
    letters = [letter for letter, _n, _d in census.SHAPE_CLASSES]
    assert letters == list("ABCDEFGHIJK")
    assert all(census.SHAPE_NAMES[x] and census.SHAPE_DESCRIPTIONS[x]
               for x in letters)


@pytest.mark.parametrize("shape,source,declares", [
    ("E", VARIADIC, False),
    ("A", None, False),
    ("C", "VALUE = 1\n", False),
    ("F", "class S:\n    def f(self, *, k: int): pass\n", False),
    ("I", SINGLE_CONTAINER, True),
    ("J", SINGLE_SCALAR, True),
    ("K", TWO_SCALARS, True),
    ("G", "class S:\n    def f(self): pass\n", True),
])
def test_declares_signature_tracks_the_shape_class(shape, source, declares):
    """
    The census's headline claim is that ZERO candidates declare a signature.
    That number comes from this flag, so the flag is asserted per class rather
    than assumed to follow from the letter.

    A variadic placeholder declaring `False` is the whole point: it is what
    stops the count from claiming 1,140 questions are ready to migrate.
    """
    record = census.classify({"id": 1, "boilerplate_code": {"python": source},
                              "hidden_wrapper_code": {}})
    assert record["shape"] == shape
    assert record["declares_signature"] is declares


def test_the_candidate_population_declares_nothing():
    """The census result itself, in miniature: variadic stubs all the way."""
    records = [census.classify({"id": i, "boilerplate_code": {"python": VARIADIC},
                                "hidden_wrapper_code": {}})
               for i in range(5)]
    summary = census.summarise(records)

    assert summary["declares_signature"] == 0
    assert summary["by_shape"] == {"E": 5}
    assert summary["by_v3_requirement"] == {UNKNOWN: 5}


def test_a_shape_class_is_exclusive():
    """Each starter gets exactly one letter; the census counts questions."""
    sources = [VARIADIC, SINGLE_CONTAINER, SINGLE_SCALAR, TWO_SCALARS]
    records = [census.classify({"id": i, "boilerplate_code": {"python": s},
                                "hidden_wrapper_code": {}})
               for i, s in enumerate(sources)]
    summary = census.summarise(records)
    assert summary["total"] == len(sources)
    assert sum(summary["by_shape"].values()) == len(sources)
    assert sum(summary["by_v3_requirement"].values()) == len(sources)


# ── the projection ──────────────────────────────────────────────────────────

def test_the_projection_is_a_population_estimate_with_an_interval():
    result = census.projection({REQUIRED: 364, SUFFICIENT: 1141}, 1140)

    assert result["reference_population"] == 1505
    assert result["low"] < result["estimate"] < result["high"]
    assert 0 < result["estimate"] < 1140
    assert "NOT a per-question verdict" in result["basis"]


def test_the_projection_ignores_unknowns_in_the_reference_class():
    """
    A rate computed over questions whose verdict is UNKNOWN is not a rate. The
    denominator is the DECLARED population only.
    """
    without = census.projection({REQUIRED: 1, SUFFICIENT: 3}, 100)
    with_unknowns = census.projection(
        {REQUIRED: 1, SUFFICIENT: 3, UNKNOWN: 900}, 100)

    assert without == with_unknowns
    assert without["reference_population"] == 4


def test_no_reference_data_yields_no_projection():
    """Silence rather than a fabricated rate."""
    assert census.projection({}, 1140) is None
    assert census.projection({UNKNOWN: 500}, 1140) is None
    assert census.projection({REQUIRED: 10, SUFFICIENT: 10}, 0) is None


def test_a_single_container_binds_as_one_argument_not_many():
    invocation = ea.build_invocation("[1, 2, 3]", SINGLE_CONTAINER)
    assert invocation.arguments == [[1, 2, 3]]


def test_two_scalars_bind_from_two_lines():
    """
    One line per parameter — and a `str` parameter takes the line as RAW
    TEXT, not as JSON. So `abc` is the string abc, while `"abc"` is a
    five-character string that still has its quotes. Worth pinning: it is the
    difference between a hidden case that reads correctly and one that is
    silently off by two characters.
    """
    assert ea.build_invocation("abc\n2", TWO_SCALARS).arguments == ["abc", 2]
    assert ea.build_invocation('"abc"\n2', TWO_SCALARS).arguments == \
        ['"abc"', 2]


def test_a_malformed_input_is_refused_not_guessed():
    invocation = ea.build_invocation("not json at all", SINGLE_CONTAINER)
    assert not invocation.ok
    assert invocation.outcome in (ea.CONTRACT_MISMATCH, ea.INVALID_INPUT)


def test_a_non_string_stdin_is_refused():
    invocation = ea.build_invocation(["already", "parsed"], SINGLE_CONTAINER)
    assert not invocation.ok
    assert invocation.outcome == ea.INVALID_INPUT


def test_an_arity_mismatch_is_refused():
    invocation = ea.build_invocation('"a"\n"b"\n"c"', TWO_SCALARS)
    assert not invocation.ok
    assert invocation.outcome == ea.CONTRACT_MISMATCH


# ═════════════════════════════════════════════════════════════
# The census itself must never write
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_classification_is_pure(topic):
    """Shape classification reads source text and touches nothing."""
    row = question(topic, VARIADIC)
    before = row.boilerplate_code["python"]

    census.v3_requirement(before)
    census.classify_shape(before)
    census.classify({"id": row.id, "boilerplate_code": row.boilerplate_code,
                     "hidden_wrapper_code": {}})
    ea.declared_signature(before)
    ea.public_method_names(before)
    ast.parse(before)

    row.refresh_from_db()
    assert row.boilerplate_code["python"] == before
    assert row.execution_contract_version == "v1"


def test_the_census_module_contains_no_write_call():
    """
    The census is defined as read-only, so the definition is enforced rather
    than promised. AST identifier scan, not a substring search — a docstring
    saying "never save()" must not be what makes this pass.
    """
    import inspect

    from groups import reseed_contract_census

    tree = ast.parse(inspect.getsource(reseed_contract_census))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}

    assert not called & {"save", "create", "update", "delete", "bulk_update",
                         "bulk_create", "get_or_create", "update_or_create",
                         "atomic", "select_for_update"}


@pytest.mark.django_db
def test_the_census_command_declares_no_write_and_opens_no_transaction():
    import inspect

    from groups.management.commands import reseed_contract_census as command

    tree = ast.parse(inspect.getsource(command))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}

    assert not called & {"save", "create", "delete", "update", "atomic",
                         "select_for_update", "bulk_create", "bulk_update"}
    # `values` and `values_list` are the only ORM verbs it is allowed to use.
    assert called & {"values", "values_list"}


# ═════════════════════════════════════════════════════════════
# 11A — the candidate predicate, and why the count moved
# ═════════════════════════════════════════════════════════════

def test_the_candidate_clauses_match_statement_blockers():
    """
    The census must count the population the AUTHORING path would accept. If
    these two drift, the census describes a set that cannot be reseeded.
    """
    import inspect

    from groups import reseed_authoring
    from groups.management.commands import reseed_contract_census as command

    source = inspect.getsource(reseed_authoring.stub_blockers) + \
        inspect.getsource(reseed_authoring.statement_blockers)

    # every clause the command tests is a condition the authoring path checks
    assert "STATUS_DRAFT" in source and "TRUST_UNVERIFIED" in source
    assert "hidden_test_cases" in source
    assert "QuestionApproval" in source and "OracleExecution" in source
    assert "PLACEHOLDER_MARKER" in source
    assert set(command.CLAUSE_NAMES) == {
        "draft", "unverified", "no_cases", "no_approval", "no_execution",
        "placeholder_marker"}


@pytest.mark.django_db
def test_a_question_with_stored_cases_is_not_a_candidate(topic):
    """
    q2201 in production: DRAFT, UNVERIFIED, marker present, but four stored
    hidden tests. Authoring over it would change what those tests test, so it
    is excluded — and excluding it is the difference between 1,141 and 1,140.
    """
    from groups.management.commands.reseed_contract_census import clause_results

    row = {"id": 2201, "status": Question.STATUS_DRAFT,
           "trust_state": Question.TRUST_UNVERIFIED,
           "hidden_test_cases": [{"input": "1", "output": "1"}],
           "content": Question.PLACEHOLDER_MARKER + " something"}
    clauses = clause_results(row, set(), set())

    assert clauses["no_cases"] is False
    assert [name for name, ok in clauses.items() if not ok] == ["no_cases"]


@pytest.mark.django_db
def test_a_question_without_the_marker_is_not_a_candidate(topic):
    """
    q92 in production: clean on every other clause but carrying a REAL
    statement. Authoring over it would overwrite content a human may have
    written. The other half of the 1,141/1,140 discrepancy.
    """
    from groups.management.commands.reseed_contract_census import clause_results

    row = {"id": 92, "status": Question.STATUS_DRAFT,
           "trust_state": Question.TRUST_UNVERIFIED,
           "hidden_test_cases": [], "content": "<p>A real statement.</p>"}
    clauses = clause_results(row, set(), set())

    assert [name for name, ok in clauses.items() if not ok] == \
        ["placeholder_marker"]


@pytest.mark.django_db
def test_remediate_contract_refuses_a_question_with_no_cases(topic):
    """
    The ordering constraint the census exposed: the existing contract command
    requires stored cases, and a reseed candidate has none. It cannot perform
    the migration at the point in the pipeline where the migration is needed.
    """
    import inspect

    from groups.management.commands import remediate_contract

    source = inspect.getsource(remediate_contract)
    assert "the question stores no test cases" in source
    assert "demonstrates that the contract executes" in source
