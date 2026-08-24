"""
Presentation quality of a generated statement (M2 P2.7h-22).

Phase 7 shipped five artifacts that were 5/5 conformant and 1/5 usable. These
tests hold the line that closed that gap, and — just as importantly — the line
that stops it becoming a nuisance: Phase 7 also demonstrated that an
overly broad vocabulary rule produces false positives faster than it produces
findings, so half of this file is legitimate statements that must NOT be
refused.
"""

import pytest

from groups import reseed_presentation as pres

# A statement of the shape the pilot's best artifact had.
GOOD = (
    "<h3>Problem Statement</h3>"
    "<p>You are given a list of integers <code>nums</code> and an integer "
    "<code>target</code>. Count how many values in the list are equal to the "
    "target and return that count.</p>"
    "<h3>Constraints</h3><ul><li>1 &le; len(nums) &le; 100</li></ul>"
    "<h3>Example</h3><p>nums = [1, 2, 2, 3], target = 2 → Result: 2</p>")

# The q1974 shape: the specification's form, pasted.
TRANSCRIBED = (
    "<h3>Objective</h3><p>Count matching widgets.</p>"
    "<h3>Required operation</h3><p>Walk the list and count each match.</p>"
    "<h3>Input semantics</h3><p>Two parameters, nums and target.</p>"
    "<h3>Output semantics</h3><p>A single integer.</p>"
    "<h3>Constraints</h3><p>Between 1 and 100 integers.</p>"
    "<h3>Example</h3><p>nums = [1, 2], target = 2 → Result: 1</p>")


def refusals(html, **kwargs):
    return pres.presentation_refusals(html, **kwargs)


# ═════════════════════════════════════════════════════════════
# 8A.1 — specification field labels
# ═════════════════════════════════════════════════════════════

def test_a_natural_statement_passes():
    assert refusals(GOOD, parameters=["nums", "target"]) == []


def test_internal_field_labels_are_refused():
    found = pres.label_refusals(TRANSCRIBED)
    assert found
    assert any("input semantics" in refusal for refusal in found)


def test_the_labels_come_from_the_schema_not_a_blacklist():
    """
    A field added to the specification schema is detected without anyone
    remembering to list it here. That is the difference between a rule and a
    blacklist: the blacklist is always one field out of date.
    """
    from groups import reseed_specification

    for field in reseed_specification.REQUIRED_PROSE:
        assert field.replace("_", " ") in pres._SCHEMA_LABELS.values()


def test_a_label_written_inline_is_the_same_leak():
    inline = ("<p>Input semantics: two parameters, nums and target. Count the "
              "matches and return the count. For nums = [1,2] the result is "
              "1.</p>")
    assert any("inline" in refusal for refusal in pres.label_refusals(inline))


def test_a_bold_lead_in_is_a_heading_in_disguise():
    disguised = TRANSCRIBED.replace("<h3>", "<p><strong>").replace(
        "</h3>", "</strong></p>")
    assert pres.label_refusals(disguised)


# ═════════════════════════════════════════════════════════════
# 8A.2 — author-directed commentary
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sentence", [
    "The greatest common divisor of the whole list is NOT what is wanted.",
    "Do not confuse this with the sum of the elements.",
    "The model should return the count rather than the list.",
    "The generator should avoid renaming the method.",
    "The specification requires that both neighbours match.",
    "This wording is intended to rule out the adjacent case.",
    "Unlike the previous problem, order does not matter here.",
    "Note to the author: check the edge case.",
])
def test_author_directed_prose_is_refused(sentence):
    html = GOOD.replace("</h3><p>You are given",
                        f"</h3><p>{sentence} You are given")
    found = pres.commentary_refusals(html)
    assert found, sentence


@pytest.mark.parametrize("sentence", [
    "Return the result.",
    "Do not modify the input array.",
    "Do not use built-in sorting functions.",
    "You must not allocate additional storage.",
    "The objective is to minimise the number of moves.",
    "Apply the operation until no further moves are possible.",
    "The input is guaranteed to contain at least one element.",
    "Your output should be a single integer.",
    "Make sure every element is examined exactly once.",
    "This operation may be repeated any number of times.",
])
def test_legitimate_learner_instructions_are_not_refused(sentence):
    """
    8C. Phase 7 proved that broad vocabulary rules produce false positives
    faster than findings. Every sentence here contains a word the checks care
    about and is ordinary problem prose.
    """
    html = GOOD.replace("</h3><p>You are given",
                        f"</h3><p>{sentence} You are given")
    assert pres.commentary_refusals(html) == [], sentence


def test_one_finding_per_overlapping_pattern():
    """Several patterns deliberately overlap; the reviewer sees one finding."""
    html = GOOD.replace("</h3><p>You", "</h3><p>That is not what is wanted. You")
    assert len(pres.commentary_refusals(html)) == 1


# ═════════════════════════════════════════════════════════════
# 8A.3 — transcription
# ═════════════════════════════════════════════════════════════

def test_transcribing_the_specification_form_is_refused():
    found = pres.transcription_refusals(TRANSCRIBED)
    assert found
    assert "reproduces the specification's structure" in found[0]


def test_ordinary_problem_headings_are_not_transcription():
    """
    "Constraints" then "Edge cases" is the shape of every problem statement
    ever written. Both happen to be schema fields; neither is a leak.
    """
    natural = (
        "<h3>Problem</h3><p>You are given <code>nums</code>. Count the values "
        "equal to <code>target</code> and return the count.</p>"
        "<h3>Constraints</h3><p>1 &le; len(nums) &le; 100</p>"
        "<h3>Edge cases</h3><p>An empty match returns zero.</p>"
        "<h3>Example</h3><p>nums = [1, 2], target = 2 → Result: 1</p>")
    assert pres.transcription_refusals(natural) == []
    assert refusals(natural, parameters=["nums", "target"]) == []


def test_legitimate_terminology_overlap_is_not_transcription():
    """Using the specification's WORDS is required by conformance. Only its
    FORM is refused here."""
    overlapping = (
        "<h3>Problem</h3><p>Walk the list and count each element equal to the "
        "target. Every element is examined and equality is exact. Return the "
        "count.</p><h3>Example</h3><p>nums = [1, 2], target = 2 → 1</p>")
    assert pres.transcription_refusals(overlapping) == []


# ═════════════════════════════════════════════════════════════
# 8A.4 — the worked example
# ═════════════════════════════════════════════════════════════

def test_a_statement_without_an_example_is_refused():
    without = ("<h3>Problem</h3><p>You are given <code>nums</code> and "
               "<code>target</code>; count the matches and return the count. "
               "The list holds between 1 and 100 integers.</p>")
    found = pres.example_refusals(without, parameters=["nums", "target"])
    assert any("no worked example" in refusal for refusal in found)


def test_an_example_may_present_its_input_as_an_assignment():
    """
    `nums = [12, 15]` presents an input as surely as the word "Input:". A
    cue-word-only rule refused the pilot's best artifact for exactly this.
    """
    assert pres.example_refusals(GOOD, parameters=["nums", "target"]) == []


def test_an_example_without_an_output_is_refused():
    half = (GOOD.replace("nums = [1, 2, 2, 3], target = 2 → Result: 2",
                         "nums = [1, 2, 2, 3], target = 2"))
    found = pres.example_refusals(half, parameters=["nums", "target"])
    assert any("output" in refusal for refusal in found)


def test_an_example_without_concrete_values_is_refused():
    """
    The fixture names both parameters and has input and output cues, so the
    ONLY thing wrong with it is the absence of data. An earlier version said
    "some input gives some result", which also failed the names-a-parameter
    check — and a mutation sweep proved that other refusal was masking this
    one entirely: disabling the literal check changed nothing any test saw.
    """
    vague = GOOD.replace("nums = [1, 2, 2, 3], target = 2 → Result: 2",
                         "given nums and target, the result is the count")
    found = pres.example_refusals(vague, parameters=["nums", "target"])

    assert any("no concrete value" in refusal for refusal in found), found


def test_an_example_that_names_no_parameter_is_refused():
    orphan = GOOD.replace("nums = [1, 2, 2, 3], target = 2 → Result: 2",
                          "the values [1, 2] give Result: 1")
    found = pres.example_refusals(orphan, parameters=["nums", "target"])
    assert any("declared parameter" in refusal for refusal in found)


def test_an_example_lifted_from_the_specification_is_refused():
    specification = {field: "" for field in
                     __import__("groups.reseed_specification", fromlist=["x"]
                                ).REQUIRED_PROSE}
    specification["objective"] = (
        "Count how many values in the list are equal to the target and return "
        "that count, examining every element exactly once in order")
    lifted = GOOD.replace(
        "nums = [1, 2, 2, 3], target = 2 → Result: 2",
        "Count how many values in the list are equal to the target and return "
        "that count, examining every element exactly once in order. nums = "
        "[1, 2] → 1")
    found = pres.example_refusals(lifted, parameters=["nums", "target"],
                                  specification=specification)
    assert any("lifted verbatim" in refusal for refusal in found)


def test_the_example_check_does_not_claim_correctness():
    """
    The boundary of the claim, as a test. An example whose output is
    arithmetically wrong still passes: checking it would mean running the
    problem, which is the oracle's job several phases from here.
    """
    wrong = GOOD.replace("→ Result: 2", "→ Result: 99")
    assert pres.example_refusals(wrong, parameters=["nums", "target"]) == []


# ═════════════════════════════════════════════════════════════
# 8A.5 — internal metadata
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sentence", [
    "The specification digest is ad2a9eb0.",
    "This artifact was produced by prompt_template v2.",
    "See the manifest for provenance.",
    "Provenance: OPERATOR_SUPPLIED.",
    "question_id 1974.",
    "The conformance validator accepted this.",
])
def test_internal_metadata_is_refused(sentence):
    html = GOOD.replace("</h3><p>You are given",
                        f"</h3><p>{sentence} You are given")
    assert pres.metadata_refusals(html), sentence


@pytest.mark.parametrize("sentence", [
    "Apply the XOR operator to each pair.",
    "Return a digest of the input string using the given hash model.",
    "The operator precedence follows the usual rules.",
    "Build a model of the grid before simulating.",
])
def test_domain_words_that_look_like_metadata_are_not_refused(sentence):
    """
    8C again. `operator`, `model` and `digest` are ordinary problem-domain
    words. Refusing them because a tool shares the name would make whole
    categories of question unwritable.
    """
    html = GOOD.replace("</h3><p>You are given",
                        f"</h3><p>{sentence} You are given")
    assert pres.metadata_refusals(html) == [], sentence


# ═════════════════════════════════════════════════════════════
# 8A.6 — structure
# ═════════════════════════════════════════════════════════════

def test_a_statement_that_opens_with_a_field_label_is_refused():
    opened = ("<h3>Required operation</h3><p>Walk the list and count matches, "
              "returning the count for the given nums and target values.</p>"
              "<h3>Example</h3><p>nums = [1, 2], target = 2 → 1</p>")
    assert any("opens with the specification field" in refusal
               for refusal in pres.structure_refusals(opened))


def test_a_statement_that_never_says_what_is_returned_is_refused():
    silent = ("<h3>Problem</h3><p>You are given <code>nums</code> and a "
              "<code>target</code>. Consider each value in the list carefully "
              "and think about which ones match the target value given.</p>")
    assert any("returned or produced" in refusal
               for refusal in pres.structure_refusals(silent))


def test_no_single_template_is_enforced():
    """Presentation quality, not stylistic uniformity: three different shapes,
    all acceptable."""
    shapes = [
        ("<p>Given <code>nums</code> and <code>target</code>, return how many "
         "values equal the target. Example: nums = [1,2], target = 2 gives "
         "1.</p>"),
        ("<h4>Task</h4><p>Return the number of values in <code>nums</code> "
         "equal to <code>target</code>.</p><h4>Example</h4>"
         "<p>nums = [1,2], target = 2 → 1</p>"),
        ("<h3>Counting matches</h3><p>You are given a list "
         "<code>nums</code> and a value <code>target</code>; produce the "
         "count of matching entries.</p><p>For nums = [1,2] and target = 2 "
         "the answer is 1.</p>"),
    ]
    for html in shapes:
        assert refusals(html, parameters=["nums", "target"]) == [], html[:60]


def test_the_report_is_itemised_for_a_reviewer():
    report = pres.presentation_report(TRANSCRIBED, parameters=["nums"])
    assert report["labels"] and report["transcription"]
    assert "objective" in report["headings"]
