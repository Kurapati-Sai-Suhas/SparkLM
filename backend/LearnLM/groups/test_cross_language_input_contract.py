"""
One canonical input, two harnesses, the same argument (Phase 1 M8).

THE DEFECT
    v2's stated input rule is "a line with several tokens is a sequence; a line
    with one token is a scalar" — a rule about LENGTH. Python never applied it
    as written: `_sparklm_parse` reads the parameter's annotation first, so a
    parameter declared `list[int]` gets a list at every length. The length rule
    is Python's fallback for an UNDECLARED parameter.

    JavaScript had no annotations to read, so it applied that fallback as its
    only rule:

        declared      line      python      javascript
        list[int]     "5"       [5]         5             DIVERGE
        list[str]     "cat"     ["cat"]     "cat"         DIVERGE

    `["cat"]` matters: it is the same defect with no digit in it, which is why
    the repair is not about singletons. One harness knew the declared type and
    the other did not.

WHAT THESE TESTS DO
    They EXECUTE both harnesses through `GradingService._build_executable` and
    `GradingService.prepare_stdin` — the same two functions the grader and the
    oracle reach execution through — and compare the arguments that actually
    arrive. A test that string-matched the template would prove it is spelled a
    certain way, not that a learner's method receives the same value in two
    languages.

    The probe's method returns its own arguments as compact JSON, so a failure
    reports the SHAPE that was built rather than an algorithm's answer.

WHAT IS NOT CLAIMED
    Judge0 was not used. These are local `python`/`node` subprocesses, which is
    the whole of the parser contract but none of Judge0's sandbox, limits or
    status classification. Java is not executed — there is no local JVM — and
    is asserted structurally only.
"""

import json
import subprocess
import sys
import textwrap

import pytest

from groups import execution_contract as ec
from groups.models import CodingPortal, Question, Topic
from groups.services import GENERIC_JS_WRAPPER, GradingService

# ═════════════════════════════════════════════════════════════
# The execution path — not a helper in isolation
# ═════════════════════════════════════════════════════════════

RUNNERS = {"python": [sys.executable, "-c"], "javascript": ["node", "-e"]}


def execute(question, language, learner_source, stdin):
    """
    Run one submission exactly as the grader would build and feed it.

    Both halves of the execution seam are used: `_build_executable` decides
    what code runs, `prepare_stdin` decides what it is fed. Bypassing either
    would test a parser the platform does not actually use.
    """
    executable, _stored = GradingService._build_executable(
        question, language, learner_source)
    prepared = GradingService.prepare_stdin(question, language, stdin)
    proc = subprocess.run(RUNNERS[language] + [executable], input=prepared,
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def arguments_received(question, language, annotations, stdin):
    """The arguments the learner's method was called with, as compact JSON."""
    source = (python_probe(annotations) if language == "python"
              else js_probe(len(annotations)))
    stdout, stderr, code = execute(question, language, source, stdin)
    assert code == 0, f"{language} exited {code}: {stderr}"
    return stdout


def python_probe(annotations):
    """
    A Solution whose one public method echoes its own arguments.

    Annotated to match the question's starter, because that is what a learner
    submits: they are handed the starter and edit its body. It matters that
    the annotations are HERE and not only on the question — the v2 Python
    harness reads `inspect.signature` of the submitted method, so the
    learner's own text is what it types arguments from. See
    `test_a_python_learner_who_strips_the_annotation_still_diverges` for the
    consequence when the two disagree.
    """
    names = [f"a{i}" for i in range(len(annotations))]
    parameters = ", ".join(
        name + (f": {a}" if a else "") for name, a in zip(names, annotations))
    echo = ", ".join(names)
    return textwrap.dedent(f"""
        import json
        class Solution:
            def probe(self, {parameters}):
                return json.dumps([{echo}], separators=(",", ":"))
    """)


def js_probe(count):
    """The JavaScript equivalent. No annotations exist to carry."""
    names = ", ".join(f"a{i}" for i in range(count))
    return textwrap.dedent(f"""
        class Solution {{
            probe({names}) {{ return JSON.stringify([{names}]); }}
        }}
    """)


# ═════════════════════════════════════════════════════════════
# The one canonical fixture both languages are held to
# ═════════════════════════════════════════════════════════════
#
# (label, declared annotations, stored stdin, the argument list both
#  harnesses must build). Expressed once; every parity test reads it.
#
# The stdin column is v2's stored form — one line per parameter, whitespace-
# separated tokens within a line. It is NOT modified to suit either language:
# the canonical representation is the input to this table, not its output.

CANONICAL_V2 = (
    ("empty array",       ["list[int]"],          "",         "[[]]"),
    ("singleton array",   ["list[int]"],          "5",        "[[5]]"),
    ("multi array",       ["list[int]"],          "1 2 3",    "[[1,2,3]]"),
    ("singleton strings", ["list[str]"],          "cat",      '[["cat"]]'),
    ("multi strings",     ["list[str]"],          "cat dog",  '[["cat","dog"]]'),
    ("scalar integer",    ["int"],                "5",        "[5]"),
    ("scalar string",     ["str"],                "hello",    '["hello"]'),
    ("boolean true",      ["bool"],               "true",     "[true]"),
    ("boolean false",     ["bool"],               "false",    "[false]"),
    ("undeclared scalar", [None],                 "5",        "[5]"),
    ("undeclared multi",  [None],                 "1 2 3",    "[[1,2,3]]"),
    ("sequence + scalar", ["list[int]", "int"],   "5\n9",     "[[5],9]"),
    ("scalar + sequence", ["int", "list[str]"],   "9\ncat",   '[9,["cat"]]'),
    ("two sequences",     ["list[int]", "list[int]"], "5\n7", "[[5],[7]]"),
)

CANONICAL_IDS = [row[0] for row in CANONICAL_V2]


def starter_for(annotations):
    """The Python starter that declares this row's signature."""
    parameters = ", ".join(
        f"a{i}" + (f": {a}" if a else "")
        for i, a in enumerate(annotations))
    return (f"class Solution:\n    def probe(self, {parameters}):\n"
            f"        pass\n")


@pytest.fixture
def portal_topic(db):
    portal = CodingPortal.objects.create(name="M8 Portal")
    topic, _ = Topic.objects.get_or_create(
        name="M8Topic", defaults={"structure_type": "flat", "portal": portal})
    return topic


def make_question(topic, annotations, version=ec.CONTRACT_V2, pk=None):
    return Question.objects.create(
        id=pk, title="M8", content="c", topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": starter_for(annotations)},
        hidden_test_cases=[{"stdin": "5", "expected_output": "x"}],
        hidden_wrapper_code={}, execution_contract_version=version)


# ═════════════════════════════════════════════════════════════
# The cross-language contract
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("label,annotations,stdin,expected", CANONICAL_V2,
                         ids=CANONICAL_IDS)
def test_both_harnesses_build_the_same_arguments(
        portal_topic, label, annotations, stdin, expected):
    """
    THE contract test. Same stored input, same declared signature, same
    arguments — asserted on the values, not on the text of either harness,
    because the two languages are free to print differently.
    """
    question = make_question(portal_topic, annotations)
    count = len(annotations)

    python = arguments_received(question, "python", annotations, stdin)
    javascript = arguments_received(question, "javascript", annotations, stdin)

    assert json.loads(python) == json.loads(javascript), (
        f"{label}: python built {python}, javascript built {javascript}")
    assert json.loads(python) == json.loads(expected)


@pytest.mark.django_db
def test_the_original_defect_no_longer_reproduces(portal_topic):
    """
    The reported regression, on its own so a failure names it: canonical `[5]`
    for a declared sequence reached Python as `[5]` and JavaScript as `5`.
    """
    question = make_question(portal_topic, ["list[int]"])

    assert arguments_received(question, "javascript", ["list[int]"], "5") == "[[5]]"
    assert arguments_received(question, "python", ["list[int]"], "5") == "[[5]]"


@pytest.mark.django_db
def test_the_defect_was_never_about_length(portal_topic):
    """
    `["cat"]` is the same defect with no digit in it. A fix that keyed on "the
    array has one element" would pass the singleton test and still be wrong
    here, which is why the repair reads the declared kind instead.
    """
    question = make_question(portal_topic, ["list[str]"])

    assert arguments_received(question, "javascript", ["list[str]"], "cat") == '[["cat"]]'


# ═════════════════════════════════════════════════════════════
# Each shape the current contract defines, named individually
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_an_empty_array_stays_empty(portal_topic):
    question = make_question(portal_topic, ["list[int]"])
    assert arguments_received(question, "javascript", ["list[int]"], "") == "[[]]"


@pytest.mark.django_db
def test_a_multi_element_array_is_unchanged(portal_topic):
    """The case that already worked. A repair must not trade one for another."""
    question = make_question(portal_topic, ["list[int]"])
    assert arguments_received(question, "javascript", ["list[int]"], "1 2 3") == "[[1,2,3]]"


@pytest.mark.django_db
def test_a_scalar_integer_is_still_a_scalar(portal_topic):
    """The declared kind is what decides — one token is not enough to know."""
    question = make_question(portal_topic, ["int"])
    assert arguments_received(question, "javascript", ["int"], "5") == "[5]"


@pytest.mark.django_db
def test_a_boolean_stays_a_boolean(portal_topic):
    question = make_question(portal_topic, ["bool"])
    assert arguments_received(question, "javascript", ["bool"], "true") == "[true]"
    assert arguments_received(question, "javascript", ["bool"], "false") == "[false]"


@pytest.mark.django_db
def test_arguments_keep_independent_types(portal_topic):
    """
    A sequence beside a scalar. A per-call rule could get one right by
    accident; getting both right in one invocation cannot be luck.
    """
    question = make_question(portal_topic, ["list[int]", "int"])
    assert arguments_received(question, "javascript", ["list[int]", "int"], "5\n9") == "[[5],9]"
    assert arguments_received(question, "python", ["list[int]", "int"], "5\n9") == "[[5],9]"


@pytest.mark.django_db
def test_an_undeclared_parameter_keeps_the_legacy_guess_in_both(portal_topic):
    """
    With no declared type there is nothing to honour, so the length rule
    stands — and it must stand IDENTICALLY in both languages. Matching
    Python's fallback matters as much as matching its rule.
    """
    question = make_question(portal_topic, [None])
    for stdin in ("5", "1 2 3", ""):
        assert (arguments_received(question, "javascript", [None], stdin)
                == arguments_received(question, "python", [None], stdin))


# ═════════════════════════════════════════════════════════════
# What v2 does NOT define — asserted as parity, not as support
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_v2_has_no_null_literal_and_both_languages_agree(portal_topic):
    """
    v2's tokeniser recognises numbers and the two boolean words. `null` is not
    in it, so the token stays text — in BOTH languages. Recorded as parity,
    not repaired: inventing a null literal would define an input type the
    question contract does not have.
    """
    question = make_question(portal_topic, ["str"])

    assert arguments_received(question, "javascript", ["str"], "null") == '["null"]'
    assert arguments_received(question, "python", ["str"], "null") == '["null"]'


@pytest.mark.django_db
def test_v2_cannot_express_nesting_and_both_languages_agree(portal_topic):
    """
    A v2 line is flat tokens, so `[[1],[2]]` is one token and stays text in
    both languages. Nesting is expressible under v1/v3, whose JSON envelope
    carries it — see the v1 test below. Not invented here.
    """
    question = make_question(portal_topic, ["list[int]"])
    nested = "[[1],[2]]"

    assert (arguments_received(question, "javascript", ["list[int]"], nested)
            == arguments_received(question, "python", ["list[int]"], nested))


@pytest.mark.django_db
def test_v2_does_not_honour_a_declared_string_of_digits(portal_topic):
    """
    A known v2 limitation, pinned so the M8 repair is not mistaken for having
    fixed it: `s: str` fed `5` yields the NUMBER in both languages, because
    v2's tokeniser runs before any type is consulted. `input_contract` records
    this as v2's shape-guessing defect and v3 is where it is addressed.
    """
    question = make_question(portal_topic, ["str"])

    assert arguments_received(question, "javascript", ["str"], "5") == "[5]"
    assert arguments_received(question, "python", ["str"], "5") == "[5]"


# ═════════════════════════════════════════════════════════════
# v1 — untouched, and already in agreement
# ═════════════════════════════════════════════════════════════

V1_CANONICAL = (
    ("empty array",     "[[]]",        1),
    ("singleton array", "[[5]]",       1),
    ("multi array",     "[[1,2,3]]",   1),
    ("nested array",    "[[[1],[2]]]", 1),
    ("scalar integer",  "[5]",         1),
    ("scalar string",   '["5"]',       1),
    ("boolean",         "[true]",      1),
    ("null",            "[null]",      1),
    ("object",          '[{"a":1}]',   1),
    ("two arguments",   '[[1,2],"x"]', 2),
    ("zero arguments",  "[]",          0),
)


@pytest.mark.django_db
@pytest.mark.parametrize("label,envelope,count", V1_CANONICAL,
                         ids=[row[0] for row in V1_CANONICAL])
def test_v1_already_agrees_across_languages(portal_topic, label, envelope,
                                            count):
    """
    Measured, not assumed. v1 splats the top-level array as the ARGUMENT LIST,
    so every shape — nesting and objects included — survives as JSON and the
    two harnesses have always matched. That is why the repair is confined to
    v2, and why v1, which ~2,923 production questions are graded under, is not
    touched.
    """
    question = make_question(portal_topic, [None] * count,
                             version=ec.CONTRACT_V1)

    python = arguments_received(question, "python", [None] * count, envelope)
    javascript = arguments_received(question, "javascript", [None] * count, envelope)

    assert json.loads(python) == json.loads(javascript)
    assert json.loads(python) == json.loads(envelope)


@pytest.mark.django_db
def test_a_v1_question_is_built_byte_identically(portal_topic):
    """
    No kind vector reaches v1. The M8 substitution happens on the v2 branch
    only, so the harness ~2,923 questions are graded under is unchanged text.
    """
    question = make_question(portal_topic, ["list[int]"],
                             version=ec.CONTRACT_V1)
    learner = js_probe(1)

    executable, _ = GradingService._build_executable(
        question, "javascript", learner)

    assert executable == GENERIC_JS_WRAPPER.replace("{user_code}", learner)
    assert "__sparklmKinds" not in executable


# ═════════════════════════════════════════════════════════════
# The reflection contract, still intact
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_two_public_methods_are_still_refused(portal_topic):
    """JavaScript is still a REFLECTION language and still never guesses."""
    question = make_question(portal_topic, ["list[int]"])
    source = ("class Solution {\n  probe(a) { return a; }\n"
              "  helper(a) { return a; }\n}\n")

    _out, stderr, code = execute(question, "javascript", source, "5")

    assert code == 2
    assert "exactly one public method" in stderr


@pytest.mark.django_db
def test_a_private_helper_is_still_ignored(portal_topic):
    question = make_question(portal_topic, ["list[int]"])
    source = ("class Solution {\n  probe(a) { return JSON.stringify(a); }\n"
              "  _helper(a) { return a; }\n}\n")

    stdout, _stderr, code = execute(question, "javascript", source, "5")

    assert code == 0 and stdout == "[5]"


@pytest.mark.django_db
def test_a_runtime_error_still_propagates(portal_topic):
    """Unchanged: a crash must exit non-zero, not grade as a wrong answer."""
    question = make_question(portal_topic, ["list[int]"])
    source = "class Solution {\n  probe(a) { throw new Error('boom'); }\n}\n"

    _out, _stderr, code = execute(question, "javascript", source, "5")

    assert code != 0


@pytest.mark.django_db
def test_learner_source_containing_the_placeholder_is_inert(portal_topic):
    """
    Substitution order. The kinds go in first and the learner's source last, so
    a submission containing the literal placeholder is data, never a template
    hole — the same reason these harnesses are `.replace`d and not formatted.
    """
    question = make_question(portal_topic, ["list[int]"])
    source = ("class Solution {\n"
              "  probe(a) { return JSON.stringify(['{parameter_kinds}', a]); }\n"
              "}\n")

    stdout, stderr, code = execute(question, "javascript", source, "5")

    assert code == 0, stderr
    assert stdout == '["{parameter_kinds}",[5]]'


# ═════════════════════════════════════════════════════════════
# The kind vector itself
# ═════════════════════════════════════════════════════════════

def test_the_kind_vector_reads_the_declared_signature():
    assert ec.v2_parameter_kinds(starter_for(["list[int]", "int"])) == \
        [ec.SEQUENCE_KIND, ec.SCALAR_KIND]
    assert ec.v2_parameter_kinds(starter_for([None])) == [ec.SCALAR_KIND]


def test_an_unreadable_starter_yields_no_kinds_rather_than_a_guess():
    """
    Empty means "undeclared", which every harness already has a documented
    fallback for. A fabricated vector would make JavaScript confident about a
    signature nobody declared.
    """
    assert ec.v2_parameter_kinds("") == []
    assert ec.v2_parameter_kinds("class Solution\n  def broken(") == []


def test_the_sequence_predicate_matches_the_python_harness_exactly():
    """
    The two harnesses must call the same annotations sequences. Python's is
    written inside its wrapper template as a literal substring test; this
    reads that template rather than trusting the constant, so widening one
    without the other fails here instead of in production.
    """
    for hint in ec.V2_SEQUENCE_HINTS:
        assert f'"{hint}" in str(annotation).lower()' in ec.V2_PYTHON_WRAPPER

    # And nothing WIDER leaked in from `execution_adapter`, whose sequence
    # hints also include tuple/iterable/array — reading those here would make
    # JavaScript treat `tuple[int]` as a sequence while Python v2 does not,
    # which is this milestone's own defect from the other side.
    for wider in ("tuple", "iterable", "array", "set["):
        assert wider not in ec.V2_SEQUENCE_HINTS


def test_the_javascript_parser_asks_the_kind_before_the_length():
    """
    A structural pin on the ordering. If the length rule ran first, a declared
    sequence of one element would still collapse and every parity test above
    would fail — this test says so at the source, so the reason is visible.
    """
    body = ec.V2_JS_WRAPPER.split("function __sparklmParse")[1]
    kind_check = body.index("kind === ")
    length_check = body.index("values.length === 1")

    assert kind_check < length_check


def test_the_kind_vector_is_inert_in_the_python_harness():
    """
    M8's repair is to the JavaScript layer. Python's harness carries no kind
    vector, so for a NON-structural question `render_v2` changes nothing about
    it — asserted rather than assumed, because a stray substitution would
    change the harness every v2 Python question runs under.

    (M5 later gave the Python template a structural prelude placeholder, which
    renders to the empty string here because no parameter is a structure. That
    is why the comparison resolves the placeholder rather than ignoring it.)
    """
    baseline = ec.V2_PYTHON_WRAPPER.replace("{structural_prelude_python}", "")

    rendered = ec.render_v2(ec.V2_PYTHON_WRAPPER, "USER", ["sequence"])

    assert rendered == baseline.replace("{user_code}", "USER").replace(
        "{return_kind}", "")
    assert "__sparklmKinds" not in rendered


def test_the_java_harness_is_byte_identical():
    rendered = ec.render_v2(ec.V2_JAVA_WRAPPER, "USER", ["sequence"])

    assert rendered == ec.V2_JAVA_WRAPPER.replace("{user_code}", "USER")


def test_no_placeholder_survives_rendering():
    for template in ec.V2_WRAPPERS.values():
        rendered = ec.render_v2(template, "USER", [])
        for placeholder in ("{parameter_kinds}", "{sequence_kind}",
                            "{user_code}"):
            assert placeholder not in rendered, placeholder


@pytest.mark.django_db
def test_a_python_learner_who_strips_the_annotation_still_diverges(
        portal_topic):
    """
    A KNOWN RESIDUAL, pinned rather than hidden.

    The two harnesses read the declared type from different places: the v2
    Python harness reads `inspect.signature` of the SUBMITTED method, while
    JavaScript — which has no annotations — is given the QUESTION's declared
    kinds. For the starter as handed those are the same signature, which is
    the path M8 repairs. A Python learner who deletes the annotation has
    stepped off the declared contract, and Python falls back to the length
    rule while JavaScript still honours the question's declaration.

    Strictly narrower than before: the divergence used to hold for every
    submission, and now holds only when a learner contradicts the starter.
    Closing it fully means the Python harness reading the same injected
    vector, which changes a harness this milestone was not scoped to touch.
    """
    question = make_question(portal_topic, ["list[int]"])
    stripped = textwrap.dedent("""
        import json
        class Solution:
            def probe(self, a0):
                return json.dumps([a0], separators=(",", ":"))
    """)

    stdout, _stderr, code = execute(question, "python", stripped, "5")

    assert code == 0
    assert stdout == "[5]"          # the LENGTH rule: a bare scalar argument
    assert arguments_received(
        question, "javascript", ["list[int]"], "5") == "[[5]]"


@pytest.mark.django_db
def test_the_javascript_result_does_not_depend_on_the_submitted_source(
        portal_topic):
    """
    The other half of that story, and the reason the vector comes from the
    question: JavaScript carries no types, so two different correct
    submissions must receive the same arguments.
    """
    question = make_question(portal_topic, ["list[int]"])
    terse = "class Solution {\n  probe(a) { return JSON.stringify(a); }\n}\n"
    verbose = ("class Solution {\n  probe(numbers) {\n"
               "    const copy = numbers.slice();\n"
               "    return JSON.stringify(copy);\n  }\n}\n")

    assert execute(question, "javascript", terse, "5")[0] == "[5]"
    assert execute(question, "javascript", verbose, "5")[0] == "[5]"


def test_the_rendered_vector_is_valid_javascript():
    rendered = ec.render_v2(ec.V2_JS_WRAPPER, js_probe(1), ["sequence"])
    proc = subprocess.run(["node", "--check", "-"], input=rendered,
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode == 0, proc.stderr
