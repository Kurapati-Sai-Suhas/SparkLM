"""
Blast-radius analysis for the v1 contract (M2 P2.7 §10).

The report this feeds is used to decide whether the answer bank can be trusted,
so a miscount is not a cosmetic bug — undercounting says "fewer questions are
broken than really are" and licenses a reseed that should not happen.

NO DATABASE, NO EXECUTION. `SimpleTestCase` and synthetic dicts throughout.
"""

from django.test import SimpleTestCase

from groups import contract_impact


TEXT_STARTER = "class Solution:\n    def solve(self, s: str) -> bool:\n        pass\n"
INT_STARTER = "class Solution:\n    def solve(self, n: int) -> int:\n        pass\n"
BARE_STARTER = "class Solution:\n    def solve(self, s):\n        pass\n"
LIST_STARTER = "class Solution:\n    def solve(self, nums: list) -> int:\n        pass\n"
TWO_TEXT_STARTER = ("class Solution:\n"
                    "    def solve(self, a: str, b: str) -> str:\n"
                    "        pass\n")


def question(**overrides):
    fields = {
        "id": 1,
        "status": "PUBLISHED",
        "trust_state": "UNVERIFIED",
        "execution_contract_version": "v1",
        "boilerplate_code": {"python": TEXT_STARTER},
        "hidden_test_cases": [{"stdin": "110", "expected_output": "true"}],
        "hidden_wrapper_code": {},
    }
    fields.update(overrides)
    return fields


class V1DeliveryTests(SimpleTestCase):
    """What the shipped wrapper actually hands to the learner's function."""

    def test_numeric_looking_input_arrives_as_a_number(self):
        self.assertIs(contract_impact.v1_delivers("110"), int)
        self.assertIs(contract_impact.v1_delivers("1.0"), float)

    def test_leading_zeros_survive_because_json_rejects_them(self):
        self.assertIs(contract_impact.v1_delivers("007"), str)
        self.assertIs(contract_impact.v1_delivers("000"), str)

    def test_json_literals_arrive_as_python_values(self):
        self.assertIs(contract_impact.v1_delivers("true"), bool)
        self.assertIs(contract_impact.v1_delivers("null"), type(None))

    def test_multi_line_input_arrives_as_one_argument_per_line(self):
        self.assertEqual(contract_impact.v1_call_arguments("110\n7"), [int, int])

    def test_one_unparseable_line_sends_the_whole_input_as_one_string(self):
        """
        The per-line branch is all-or-nothing. `hello` is not valid JSON, so
        the wrapper abandons the split entirely and passes the raw two-line
        text as a SINGLE argument — not ['hello', 7].
        """
        self.assertEqual(contract_impact.v1_call_arguments("hello\n7"), [str])

    def test_a_json_list_is_splatted_into_separate_arguments(self):
        """
        `target_method(*parsed_input)`. Stdin `[1, 2]` calls solve(1, 2), so a
        question declaring a single `nums: list` parameter is never called with
        a list at all.
        """
        self.assertEqual(contract_impact.v1_call_arguments("[1, 2]"), [int, int])
        self.assertEqual(contract_impact.v1_call_arguments("[[1], [2]]"),
                         [list, list])

    def test_a_json_object_becomes_keyword_arguments(self):
        self.assertEqual(contract_impact.v1_call_arguments('{"n": 1}'),
                         contract_impact.KWARGS)

    def test_ordinary_text_arrives_as_one_string_argument(self):
        self.assertEqual(contract_impact.v1_call_arguments("hello"), [str])
        self.assertIs(contract_impact.v1_delivers("hello"), str)

    def test_stdin_is_stripped_the_way_the_wrapper_strips_it(self):
        """
        `sys.stdin.read().strip()` strips more than JSON tolerates: Python's
        `str.strip()` removes a non-breaking space, JSON does not. Seed data
        pasted from a web page carries exactly that character, and skipping the
        strip would classify `\\xa0110` as text where production sees an int.
        """
        self.assertEqual(contract_impact.v1_call_arguments("\xa0110"), [int])
        self.assertEqual(contract_impact.v1_call_arguments("110\x0b"), [int])

    def test_blank_input_passes_no_arguments_at_all(self):
        """
        `args = []` — the function is called with zero arguments. A question
        whose signature takes a parameter cannot run on blank stdin.
        """
        self.assertEqual(contract_impact.v1_call_arguments(""), [])
        self.assertEqual(contract_impact.v1_call_arguments("   \n  "), [])

    def test_retyping_detection_covers_every_passed_argument(self):
        self.assertTrue(contract_impact.v1_retypes_to_non_text("110"))
        self.assertTrue(contract_impact.v1_retypes_to_non_text("110\n7"))
        self.assertFalse(contract_impact.v1_retypes_to_non_text("hello\n7"))
        self.assertFalse(contract_impact.v1_retypes_to_non_text("007"))
        self.assertFalse(contract_impact.v1_retypes_to_non_text("hello"))


class SignatureTests(SimpleTestCase):

    def test_the_declared_parameters_are_read(self):
        name, parameters = contract_impact.declared_signature(TEXT_STARTER)
        self.assertEqual(name, "solve")
        self.assertEqual(parameters, [("s", "str")])

    def test_self_is_not_a_parameter(self):
        _name, parameters = contract_impact.declared_signature(TEXT_STARTER)
        self.assertNotIn("self", [name for name, _ in parameters])

    def test_a_plain_function_starter_is_read_too(self):
        name, parameters = contract_impact.declared_signature(
            "def count_ones(s: str):\n    pass\n")
        self.assertEqual(name, "count_ones")
        self.assertEqual(parameters, [("s", "str")])

    def test_alphabetical_method_choice_matches_the_wrapper(self):
        """
        v1 calls `dir(sol)[0]`, which is alphabetical. `check` therefore wins
        over `solve` — so the analysis must read `check`'s signature, not the
        one a human would assume.
        """
        source = ("class Solution:\n"
                  "    def solve(self, s: str):\n        pass\n"
                  "    def check(self, n: int):\n        pass\n")
        name, parameters = contract_impact.declared_signature(source)
        self.assertEqual(name, "check")
        self.assertEqual(parameters, [("n", "int")])

    def test_unparseable_starter_yields_no_signature(self):
        self.assertIsNone(contract_impact.declared_signature("def (:"))
        self.assertIsNone(contract_impact.declared_signature(""))

    def test_text_annotations_are_recognised_but_containers_are_not(self):
        self.assertTrue(contract_impact.declares_text("str"))
        self.assertTrue(contract_impact.declares_text("optional[str]"))
        self.assertFalse(contract_impact.declares_text("list[str]"))
        self.assertFalse(contract_impact.declares_text("dict[str, int]"))
        self.assertFalse(contract_impact.declares_text("int"))
        self.assertFalse(contract_impact.declares_text(""))


class ClassificationTests(SimpleTestCase):

    def test_a_declared_string_handed_an_int_is_flagged(self):
        finding = contract_impact.classify(question())
        self.assertIn("text_retyped", finding["reasons"])
        self.assertEqual(finding["retyped_cases"], [1])

    def test_a_declared_string_handed_a_string_is_clean(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "007", "expected_output": "true"}]))
        self.assertNotIn("text_retyped", finding["reasons"])

    def test_an_int_parameter_handed_an_int_is_clean(self):
        finding = contract_impact.classify(question(
            boilerplate_code={"python": INT_STARTER}))
        self.assertNotIn("text_retyped", finding["reasons"])

    def test_varying_argument_types_across_cases_are_flagged(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "110", "expected_output": "true"},
                               {"stdin": "hello", "expected_output": "false"}]))
        self.assertIn("type_unstable", finding["reasons"])

    def test_consistent_argument_types_are_not_flagged_unstable(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "110", "expected_output": "true"},
                               {"stdin": "1011", "expected_output": "false"}]))
        self.assertNotIn("type_unstable", finding["reasons"])

    def test_python_cased_booleans_are_flagged(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "hello", "expected_output": "True"}]))
        self.assertIn("boolean_casing", finding["reasons"])
        self.assertEqual(finding["boolean_casing_cases"], 1)

    def test_lowercase_booleans_are_not_flagged(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "hello", "expected_output": "true"}]))
        self.assertNotIn("boolean_casing", finding["reasons"])

    def test_blank_stdin_is_flagged(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "", "expected_output": "0"}]))
        self.assertIn("blank_stdin", finding["reasons"])

    def test_multiple_public_methods_are_flagged_as_ambiguous(self):
        source = ("class Solution:\n"
                  "    def solve(self, s: str):\n        pass\n"
                  "    def helper(self, s: str):\n        pass\n")
        finding = contract_impact.classify(question(
            boilerplate_code={"python": source}))
        self.assertIn("ambiguous_entry_point", finding["reasons"])
        self.assertEqual(finding["public_methods"], ["helper", "solve"])

    def test_an_unannotated_starter_is_recorded_not_accused(self):
        finding = contract_impact.classify(question(
            boilerplate_code={"python": BARE_STARTER}))
        self.assertIn("unannotated", finding["reasons"])
        self.assertNotIn("text_retyped", finding["reasons"])

    def test_a_custom_wrapper_is_excluded_from_analysis(self):
        """
        `_build_executable` checks the per-question wrapper FIRST, so the
        generic harness never runs and its defect cannot apply.
        """
        finding = contract_impact.classify(question(
            hidden_wrapper_code={"python": "# harness\n{user_code}\n"}))
        self.assertEqual(finding["reasons"], ["custom_wrapper"])
        self.assertFalse(finding["analysable"])

    def test_a_blank_custom_wrapper_does_not_count_as_one(self):
        finding = contract_impact.classify(question(
            hidden_wrapper_code={"python": "   "}))
        self.assertNotIn("custom_wrapper", finding["reasons"])

    def test_a_question_without_python_starter_is_not_analysable(self):
        finding = contract_impact.classify(question(boilerplate_code={}))
        self.assertIn("no_python_starter", finding["reasons"])
        self.assertFalse(finding["analysable"])

    def test_malformed_test_cases_are_reported_not_crashed_on(self):
        finding = contract_impact.classify(question(
            hidden_test_cases="not a list"))
        self.assertIn("malformed_test_cases", finding["reasons"])

    def test_a_non_dict_case_is_reported_not_crashed_on(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "hi", "expected_output": "x"}, "junk"]))
        self.assertIn("malformed_test_cases", finding["reasons"])

    def test_a_list_valued_expected_output_is_counted_not_crashed_on(self):
        """
        Real production shape, and a severe one: `GradingService.grade` calls
        `.strip()` on `expected_output` without checking, so a list raises
        AttributeError inside the grader and the submission returns no verdict
        at all. Counted here rather than allowed to crash the analysis — the
        report exists to find these, not to fall over on them.
        """
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "abc", "expected_output": ["a", "b"]}]))
        self.assertIn("non_string_test_field", finding["reasons"])
        self.assertEqual(finding["non_string_fields"], ["expected_output@case1"])

    def test_a_list_valued_stdin_is_counted_too(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": [1, 2], "expected_output": "x"}]))
        self.assertIn("non_string_test_field", finding["reasons"])
        self.assertEqual(finding["non_string_fields"], ["stdin@case1"])

    def test_literal_backslash_n_is_expanded_before_analysis(self):
        """
        Test-case stdin stores `\\n` as two characters and both services expand
        it. Unexpanded, `110\\n7` is one unparseable line and looks like a
        single string argument; expanded, it is two integers. Skipping the
        expansion would undercount multi-argument questions.
        """
        finding = contract_impact.classify(question(
            boilerplate_code={"python": TWO_TEXT_STARTER},
            hidden_test_cases=[{"stdin": "110\\n7", "expected_output": "x"}]))
        self.assertIn("text_retyped", finding["reasons"])
        self.assertNotIn("arity_mismatch", finding["reasons"])


class PositionalMatchingTests(SimpleTestCase):
    """
    The claim is about ONE parameter and the value that reaches it. "Some
    parameter is text and some argument is not" flags correct questions.
    """

    def test_a_string_and_a_list_parameter_called_correctly_is_clean(self):
        """
        `wordBreak(s: str, wordDict: list[str])` handed `"cat"` and `["a"]` is
        called exactly as declared. A coarse any/any check flags it because a
        list is not a string; that is a false accusation.
        """
        source = ("class Solution:\n"
                  "    def wordBreak(self, s: str, wordDict: list[str]) -> bool:\n"
                  "        pass\n")
        finding = contract_impact.classify(question(
            boilerplate_code={"python": source},
            hidden_test_cases=[{"stdin": '"cat"\\n["c","at"]',
                                "expected_output": "true"}]))
        self.assertNotIn("text_retyped", finding["reasons"])
        self.assertNotIn("arity_mismatch", finding["reasons"])

    def test_the_text_parameter_itself_being_retyped_is_flagged(self):
        source = ("class Solution:\n"
                  "    def solve(self, s: str, n: int) -> str:\n        pass\n")
        finding = contract_impact.classify(question(
            boilerplate_code={"python": source},
            hidden_test_cases=[{"stdin": "110\\n3", "expected_output": "x"}]))
        self.assertIn("text_retyped", finding["reasons"])

    def test_a_non_text_parameter_being_retyped_is_not_this_finding(self):
        source = ("class Solution:\n"
                  "    def solve(self, n: int, s: str) -> str:\n        pass\n")
        finding = contract_impact.classify(question(
            boilerplate_code={"python": source},
            hidden_test_cases=[{"stdin": '3\\n"abc"', "expected_output": "x"}]))
        self.assertNotIn("text_retyped", finding["reasons"])

    def test_the_predicate_is_positional(self):
        parameters = [("s", "str"), ("nums", "list[int]")]
        self.assertFalse(
            contract_impact.positional_text_mismatch(parameters, [str, list]))
        self.assertTrue(
            contract_impact.positional_text_mismatch(parameters, [int, list]))


class ArityTests(SimpleTestCase):
    """
    Questions v1 cannot call at all. Distinct from a wrong answer: the call
    raises TypeError before the learner's code runs, so every submission fails
    — correct ones included — and no expected output could ever be met.
    """

    def test_a_splatted_list_against_one_parameter_is_flagged(self):
        finding = contract_impact.classify(question(
            boilerplate_code={"python": LIST_STARTER},
            hidden_test_cases=[{"stdin": "[1, 2, 3]", "expected_output": "6"}]))
        self.assertIn("arity_mismatch", finding["reasons"])
        self.assertEqual(finding["declared_arity"], 1)

    def test_blank_stdin_against_one_parameter_is_flagged(self):
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "", "expected_output": "0"}]))
        self.assertIn("arity_mismatch", finding["reasons"])

    def test_blank_stdin_against_zero_parameters_is_not_flagged(self):
        """The 27 zero-arg cases are only a problem when a parameter exists."""
        finding = contract_impact.classify(question(
            boilerplate_code={"python":
                              "class Solution:\n    def solve(self):\n        pass\n"},
            hidden_test_cases=[{"stdin": "", "expected_output": "0"}]))
        self.assertNotIn("arity_mismatch", finding["reasons"])
        self.assertIn("zero_parameters", finding["reasons"])

    def test_matching_arity_is_not_flagged(self):
        """Two JSON-quoted strings do parse per line, so the call is well-formed."""
        finding = contract_impact.classify(question(
            boilerplate_code={"python": TWO_TEXT_STARTER},
            hidden_test_cases=[{"stdin": '"abc"\\n"def"', "expected_output": "x"}]))
        self.assertNotIn("arity_mismatch", finding["reasons"])
        self.assertNotIn("text_retyped", finding["reasons"])

    def test_two_bare_words_cannot_reach_a_two_parameter_function(self):
        """
        A consequence worth stating plainly: under v1 there is NO way to pass
        two unquoted strings. `abc` is not valid JSON, so the per-line split is
        abandoned and both lines arrive as one argument — a TypeError before
        the learner's code runs.
        """
        finding = contract_impact.classify(question(
            boilerplate_code={"python": TWO_TEXT_STARTER},
            hidden_test_cases=[{"stdin": "abc\\ndef", "expected_output": "x"}]))
        self.assertIn("arity_mismatch", finding["reasons"])
        self.assertEqual(finding["declared_arity"], 2)

    def test_a_variadic_starter_makes_no_arity_claim(self):
        """
        `def solve(self, *args, **kwargs)` is a placeholder, not a zero-argument
        contract. Counting it as never-callable would inflate the figure with
        questions that simply have no declared signature yet.
        """
        source = ("class Solution:\n"
                  "    def solve(self, *args, **kwargs):\n        pass\n")
        finding = contract_impact.classify(question(
            boilerplate_code={"python": source},
            hidden_test_cases=[{"stdin": "[1, 2, 3]", "expected_output": "6"}]))
        self.assertIn("variadic_starter", finding["reasons"])
        self.assertNotIn("arity_mismatch", finding["reasons"])
        self.assertTrue(contract_impact.accepts_variable_arity(source))

    def test_a_fixed_signature_is_not_variadic(self):
        self.assertFalse(contract_impact.accepts_variable_arity(TEXT_STARTER))
        self.assertFalse(contract_impact.accepts_variable_arity("def (:"))

    def test_variadic_is_judged_on_the_method_v1_would_call(self):
        """
        Alphabetical choice again: `check` is called, and it is NOT variadic,
        so the variadic `solve` must not excuse the question.
        """
        source = ("class Solution:\n"
                  "    def solve(self, *args):\n        pass\n"
                  "    def check(self, n: int):\n        pass\n")
        self.assertFalse(contract_impact.accepts_variable_arity(source))

    def test_keyword_argument_inputs_are_not_guessed_at(self):
        """
        `**parsed_input` takes its argument names from the input. Arity cannot
        be checked against the signature, so nothing is claimed.
        """
        finding = contract_impact.classify(question(
            hidden_test_cases=[{"stdin": '{"s": "110"}', "expected_output": "x"}]))
        self.assertNotIn("arity_mismatch", finding["reasons"])
        self.assertNotIn("text_retyped", finding["reasons"])


class SummaryTests(SimpleTestCase):

    def test_a_question_counts_under_every_reason_it_matches(self):
        """
        Reasons are independent, not a ranking. Counting each question once
        under its worst reason would understate the repair surface.
        """
        findings = [contract_impact.classify(question(
            hidden_test_cases=[{"stdin": "110", "expected_output": "True"},
                               {"stdin": "hello", "expected_output": "true"}]))]
        summary = contract_impact.summarise(findings)
        counts = summary["reason_counts"]
        self.assertEqual(counts["text_retyped"], 1)
        self.assertEqual(counts["type_unstable"], 1)
        self.assertEqual(counts["boolean_casing"], 1)

    def test_provably_miscalled_tracks_the_text_retyped_count(self):
        findings = [contract_impact.classify(question(id=1)),
                    contract_impact.classify(question(
                        id=2, hidden_test_cases=[{"stdin": "007",
                                                  "expected_output": "true"}]))]
        summary = contract_impact.summarise(findings)
        self.assertEqual(summary["total_questions"], 2)
        self.assertEqual(summary["provably_miscalled"], 1)

    def test_not_analysable_questions_are_counted_separately(self):
        findings = [contract_impact.classify(question(id=1, boilerplate_code={}))]
        summary = contract_impact.summarise(findings)
        self.assertEqual(summary["analysable"], 0)
        self.assertEqual(summary["not_analysable"], 1)

    def test_every_ordered_reason_appears_even_at_zero(self):
        """The report must show a zero, not omit the row — an absent line reads
        as 'not checked'."""
        summary = contract_impact.summarise([])
        for reason in contract_impact.REASON_ORDER:
            self.assertIn(reason, summary["reason_counts"])

    def test_questions_without_tests_are_excluded_from_the_denominator(self):
        """
        A question with no hidden test cases is never executed, so it belongs
        in neither the numerator nor the denominator. Leaving it in would put a
        large `variadic_starter` count next to a percentage of a set it is not
        part of.
        """
        findings = [
            contract_impact.classify(question(id=1)),
            contract_impact.classify(question(id=2, hidden_test_cases=[])),
        ]
        summary = contract_impact.summarise(findings)
        self.assertEqual(summary["with_test_cases"], 1)
        self.assertEqual(summary["without_test_cases"], 1)
        self.assertEqual(summary["total_questions"], 2)

    def test_the_full_tally_is_still_available_separately(self):
        source = ("class Solution:\n"
                  "    def solve(self, *args):\n        pass\n")
        findings = [contract_impact.classify(question(
            id=1, boilerplate_code={"python": source}, hidden_test_cases=[]))]
        summary = contract_impact.summarise(findings)
        self.assertEqual(summary["reason_counts"]["variadic_starter"], 0)
        self.assertEqual(summary["reason_counts_all"]["variadic_starter"], 1)


class SamplingTests(SimpleTestCase):
    """
    The sample decides which questions a human actually reads, so it must be
    reproducible: a second reviewer has to see the same 20 questions, not a
    fresh draw that quietly re-rolls anything inconvenient.
    """

    def population(self):
        """
        Includes questions matching SEVERAL strata at once, because that is the
        real shape of the bank and the only arrangement in which double
        sampling is even possible.
        """
        findings = []
        for question_id in range(1, 61):
            remainder = question_id % 4
            if remainder == 0:                            # text_retyped
                cases = [{"stdin": "110", "expected_output": "true"}]
            elif remainder == 1:                          # boolean_casing
                cases = [{"stdin": "abc", "expected_output": "True"}]
            elif remainder == 2:                          # both, plus unstable
                cases = [{"stdin": "110", "expected_output": "True"},
                         {"stdin": "abc", "expected_output": "true"}]
            else:                                          # clean
                cases = [{"stdin": "abc", "expected_output": "yes"}]
            findings.append(contract_impact.classify(
                question(id=question_id, hidden_test_cases=cases)))
        return findings

    def test_the_population_spans_multiple_strata_per_question(self):
        """Guards the fixture itself: without overlap, `test_no_question_is_
        sampled_twice` cannot fail even if deduplication were removed."""
        strata = contract_impact.stratify(self.population())
        overlapping = set(strata["text_retyped"]) & set(strata["boolean_casing"])
        self.assertTrue(overlapping)

    def test_the_same_seed_selects_the_same_questions(self):
        first = contract_impact.stratified_sample(self.population(), 20, seed=7)
        second = contract_impact.stratified_sample(self.population(), 20, seed=7)
        self.assertEqual(first, second)

    def test_a_different_seed_selects_a_different_sample(self):
        first = contract_impact.stratified_sample(self.population(), 20, seed=7)
        second = contract_impact.stratified_sample(self.population(), 20, seed=8)
        self.assertNotEqual([e["id"] for e in first], [e["id"] for e in second])

    def test_excluded_questions_never_appear(self):
        """1779 is the pilot question and must not be re-audited as if new."""
        population = self.population()
        excluded = {3, 6, 9, 12}
        sample = contract_impact.stratified_sample(
            population, 20, seed=7, exclude=excluded)
        self.assertFalse({e["id"] for e in sample} & excluded)

    def test_no_question_is_sampled_twice(self):
        sample = contract_impact.stratified_sample(self.population(), 20, seed=7)
        ids = [entry["id"] for entry in sample]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_stratum_is_represented(self):
        """
        Round-robin, not proportional. Proportional allocation would spend the
        whole sample on the largest class and show none of the rare ones.
        """
        sample = contract_impact.stratified_sample(self.population(), 20, seed=7)
        strata = {entry["stratum"] for entry in sample}
        self.assertIn("text_retyped", strata)
        self.assertIn("boolean_casing", strata)
        self.assertIn(contract_impact.CLEAN, strata)

    def test_clean_questions_are_a_stratum_not_an_omission(self):
        """A sample of only-broken questions cannot show whether the rest are
        actually fine."""
        strata = contract_impact.stratify(self.population())
        self.assertIn(contract_impact.CLEAN, strata)
        self.assertTrue(strata[contract_impact.CLEAN])

    def test_questions_without_test_cases_are_not_sampled(self):
        findings = [contract_impact.classify(question(id=1, hidden_test_cases=[]))]
        self.assertEqual(
            contract_impact.stratified_sample(findings, 20, seed=7), [])

    def test_a_question_appears_in_every_stratum_it_matches(self):
        findings = [contract_impact.classify(question(
            id=1, hidden_test_cases=[{"stdin": "110",
                                      "expected_output": "True"}]))]
        strata = contract_impact.stratify(findings)
        self.assertIn(1, strata["text_retyped"])
        self.assertIn(1, strata["boolean_casing"])

    def test_asking_for_more_than_exists_returns_each_question_once(self):
        """
        Draining every stratum is where deduplication is load-bearing: a
        question in three strata would otherwise be returned three times and
        inflate a 20-question review into fewer than 20 distinct questions.
        """
        sample = contract_impact.stratified_sample(
            self.population(), 500, seed=7)
        ids = [entry["id"] for entry in sample]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 60)


class ReadOnlyTests(SimpleTestCase):
    """The module cannot reach the database or Judge0, by construction."""

    def test_the_module_imports_nothing_beyond_the_standard_library(self):
        import ast
        import inspect
        import pathlib

        tree = ast.parse(
            pathlib.Path(inspect.getfile(contract_impact)).read_text("utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported, {"ast", "json", "random", "groups"})

    def test_the_only_groups_import_is_the_shared_adapter(self):
        """
        The one permitted dependency, and the point of it: signature reading
        is IMPORTED from `execution_adapter`, not copied. Two parsers that
        agree today disagree after the next edit, and this module's job is to
        predict what the adapter will do.
        """
        import ast
        import inspect
        import pathlib

        tree = ast.parse(
            pathlib.Path(inspect.getfile(contract_impact)).read_text("utf-8"))
        from_groups = {alias.name for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom)
                       and node.module == "groups"
                       for alias in node.names}
        self.assertEqual(from_groups, {"execution_adapter"})

    def test_the_signature_readers_are_the_adapters_own(self):
        from groups import execution_adapter
        self.assertIs(contract_impact.declared_signature,
                      execution_adapter.declared_signature)
        self.assertIs(contract_impact.accepts_variable_arity,
                      execution_adapter.accepts_variable_arity)
        self.assertIs(contract_impact.chosen_function,
                      execution_adapter.chosen_function)

    def test_the_command_never_writes(self):
        """
        AST, not text search: a docstring mentioning `save` must not satisfy
        this, and a real `.save()` must not slip past it.
        """
        import ast
        import inspect

        from groups.management.commands import contract_impact as command
        tree = ast.parse(inspect.getsource(command))
        forbidden = {"save", "create", "update", "delete", "bulk_create",
                     "bulk_update", "get_or_create", "update_or_create"}
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        self.assertEqual(called & forbidden, set())
