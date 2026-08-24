"""
Question-bank census and its connection gates (M2 P2.7e).

The gates get most of the weight. A census that miscounts is a bug; a census
that reports development numbers under a production heading is a false
statement about the system, and the whole phase exists to prevent that.
"""

import ast
import inspect
import json
import pathlib
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from groups import census, census_gates
from groups.conftest import approved_reference
from groups.models import (
    CodeSubmission, CodingPortal, OracleExecution, Question, QuestionApproval,
    Topic,
)


class CensusTestCase(TestCase):

    def setUp(self):
        self.User = get_user_model()
        self.operator = self.User.objects.create_user(
            username="census-op", email="co@t.test", password="Pv#2026xyz",
            is_staff=True)
        portal = CodingPortal.objects.create(name="Census Portal")
        self.topic = Topic.objects.create(name="CensusTopic",
                                          structure_type="flat", portal=portal)

    def cases(self, count=12, expected="1", stdin=None):
        return [{"stdin": stdin or f"{n}\n", "expected_output": expected}
                for n in range(1, count + 1)]

    def question(self, title="Q", *, cases=None, status=None, trust=None,
                 boilerplate=None, content="c"):
        return Question.objects.create(
            title=title, content=content, topic=self.topic,
            base_difficulty=1200.0,
            hidden_test_cases=self.cases() if cases is None else cases,
            boilerplate_code=({"python": "x"} if boilerplate is None
                              else boilerplate),
            hidden_wrapper_code={},
            status=status or Question.STATUS_PUBLISHED,
            trust_state=trust or Question.TRUST_UNVERIFIED)


# ═════════════════════════════════════════════════════════════
# Gates — the load-bearing part
# ═════════════════════════════════════════════════════════════

class GateTests(TestCase):

    def test_host_classification(self):
        for host, expected in (
                ("127.0.0.1", "LOOPBACK"), ("localhost", "LOOPBACK"),
                ("::1", "LOOPBACK"), ("10.0.0.5", "PRIVATE"),
                ("192.168.1.9", "PRIVATE"), ("172.16.4.4", "PRIVATE"),
                ("8.8.8.8", "PUBLIC_IP"),
                ("ep-abc.aws.neon.tech", "REMOTE_HOSTNAME"),
                ("", "EMPTY"), (None, "EMPTY")):
            self.assertEqual(census_gates.classify_host(host), expected, host)

    def test_unknown_alias_is_refused(self):
        with self.assertRaises(census_gates.GateFailure) as caught:
            census_gates.gate_alias("production")
        self.assertIn("no database alias", str(caught.exception))

    def test_loopback_is_refused_without_the_explicit_flag(self):
        """
        The central gate. The test database IS loopback, so this is the real
        configuration the command would meet on a developer machine.
        """
        config = census_gates.gate_alias("default")
        with self.assertRaises(census_gates.GateFailure) as caught:
            census_gates.gate_not_local(config, allow_non_production=False)
        message = str(caught.exception)
        self.assertIn("development database", message)
        self.assertIn("worse than no census", message)

    def test_the_override_marks_the_run_non_production(self):
        """The escape hatch must not be able to produce a production label."""
        config = census_gates.gate_alias("default")
        host_class, is_production = census_gates.gate_not_local(
            config, allow_non_production=True)
        self.assertEqual(host_class, "LOOPBACK")
        self.assertFalse(is_production)

    def test_private_addresses_are_refused_too(self):
        for host in ("10.1.2.3", "192.168.0.1", "172.20.0.9"):
            with self.assertRaises(census_gates.GateFailure):
                census_gates.gate_not_local({"HOST": host},
                                            allow_non_production=False)

    def test_public_host_passes_the_locality_gate(self):
        host_class, is_production = census_gates.gate_not_local(
            {"HOST": "ep-abc.aws.neon.tech"}, allow_non_production=False)
        self.assertEqual(host_class, "REMOTE_HOSTNAME")
        self.assertTrue(is_production)

    def test_identity_is_read_from_the_server(self):
        identity = census_gates.gate_identity("default")
        for key in ("database", "role", "server_version"):
            self.assertTrue(identity[key])

    def test_write_privileges_are_detected(self):
        """
        The test role owns its database, so it CAN write — and the gate must
        say so. A gate that passed here would pass anywhere.
        """
        findings, violations = census_gates.gate_read_only("default")
        self.assertTrue(violations,
                        "the gate failed to notice a writable role")
        self.assertIn("groups_question", findings)

    def test_a_writable_role_aborts_the_whole_run(self):
        with self.assertRaises(census_gates.GateFailure) as caught:
            census_gates.run_all("default", allow_non_production=True,
                                 require_read_only=True)
        message = str(caught.exception)
        self.assertIn("WRITE privileges", message)
        self.assertIn("GRANT SELECT", message)

    def test_schema_gate_reports_applied_migrations(self):
        latest, missing = census_gates.gate_schema("default")
        self.assertIsNotNone(latest)
        self.assertEqual(missing, [], "test DB should be fully migrated")

    def test_gates_never_disclose_the_host_or_password(self):
        """
        Failure messages are operator-facing and get pasted into tickets.
        """
        config = {"HOST": "secret-host.example.com", "PASSWORD": "hunter2"}
        try:
            census_gates.gate_not_local(config, allow_non_production=False)
        except census_gates.GateFailure as failure:
            self.fail(f"public host should pass: {failure}")

        try:
            census_gates.gate_not_local({"HOST": "10.0.0.1",
                                         "PASSWORD": "hunter2"},
                                        allow_non_production=False)
        except census_gates.GateFailure as failure:
            self.assertNotIn("hunter2", str(failure))
            self.assertNotIn("10.0.0.1", str(failure))

    def test_no_gate_attempts_a_write_to_prove_read_only_ness(self):
        """
        Testing whether production mutations are possible by attempting one is
        not a test, it is the accident.

        Only strings actually passed to `cursor.execute(...)` are inspected.
        A first version scanned EVERY string constant and failed on the literal
        `"TRUNCATE"` — which is an entry in `WRITE_PRIVILEGES`, the list of
        privileges the gate ASKS ABOUT. A guard that cannot tell a privilege
        name from a statement would force the gate to stop checking for the
        most destructive privilege of all.
        """
        tree = ast.parse(pathlib.Path(
            inspect.getfile(census_gates)).read_text(encoding="utf-8"))

        executed = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (isinstance(function, ast.Attribute)
                    and function.attr == "execute"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                executed.append(node.args[0].value)

        self.assertTrue(executed, "no execute() calls found — guard is inert")
        for sql in executed:
            upper = " ".join(str(sql).upper().split())
            for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM",
                         "CREATE ", "DROP ", "ALTER ", "TRUNCATE "):
                self.assertFalse(
                    upper.startswith(verb),
                    f"gate module executes a write statement: {sql}")


# ═════════════════════════════════════════════════════════════
# Census metrics
# ═════════════════════════════════════════════════════════════

class QuestionCountTests(CensusTestCase):

    def test_empty_database(self):
        counts = census.question_counts()
        self.assertEqual(counts["total"], 0)
        self.assertEqual(counts["matrix"], {})

    def test_status_and_trust_matrix(self):
        self.question("a", status=Question.STATUS_PUBLISHED,
                      trust=Question.TRUST_UNVERIFIED)
        self.question("b", status=Question.STATUS_PUBLISHED,
                      trust=Question.TRUST_ORACLE_VERIFIED)
        self.question("c", status=Question.STATUS_DRAFT)
        self.question("d", status=Question.STATUS_BLOCKED)

        matrix = census.question_counts()["matrix"]
        self.assertEqual(matrix["PUBLISHED+UNVERIFIED"], 1)
        self.assertEqual(matrix["PUBLISHED+ORACLE_VERIFIED"], 1)
        self.assertEqual(matrix["DRAFT+UNVERIFIED"], 1)
        self.assertEqual(matrix["BLOCKED+UNVERIFIED"], 1)

    def test_trust_is_not_inferred_from_expected_output(self):
        """
        A question with a full, well-formed suite and stored answers is still
        UNVERIFIED. This is the whole point of the milestone.
        """
        self.question("legacy", cases=self.cases(count=20))
        counts = census.question_counts()
        self.assertEqual(counts["by_trust_state"].get("ORACLE_VERIFIED", 0), 0)
        self.assertEqual(counts["by_trust_state"]["UNVERIFIED"], 1)


class HiddenTestCensusTests(CensusTestCase):

    def test_counts_questions_with_and_without_tests(self):
        self.question("with")
        self.question("without", cases=[])
        counts, _dupes, _classes, _examples = census.hidden_test_census()
        self.assertEqual(counts["has_hidden_tests"], 1)
        self.assertEqual(counts["no_hidden_tests"], 1)

    def test_malformed_cases_are_counted_and_sampled(self):
        bad = self.cases(count=12)
        del bad[0]["expected_output"]
        bad[1]["stdin"] = 5                     # not a string
        self.question("bad", cases=bad)

        counts, _d, _c, examples = census.hidden_test_census()
        self.assertEqual(counts["questions_with_malformed_cases"], 1)
        self.assertTrue(examples)
        self.assertIn("question_id", examples[0])

    def test_normalized_duplicates_are_the_reported_metric(self):
        """
        '5\\n' and ' 5 ' are one case to the executor. Raw comparison would
        call them distinct; the stricter definition is reported.
        """
        cases = self.cases(count=12)
        cases[1]["stdin"] = cases[0]["stdin"].strip() + "  "
        self.question("dupes", cases=cases)

        counts, duplicates, _c, _e = census.hidden_test_census()
        self.assertEqual(counts["questions_with_normalized_duplicates"], 1)
        self.assertEqual(duplicates, 1)

    def test_minimum_count_boundary(self):
        from groups.hidden_tests import MIN_HIDDEN_TESTS
        self.question("meets", cases=self.cases(count=MIN_HIDDEN_TESTS))
        self.question("below", cases=self.cases(count=MIN_HIDDEN_TESTS - 1))
        counts, _d, _c, _e = census.hidden_test_census()
        self.assertEqual(counts["meets_minimum_count"], 1)
        self.assertEqual(counts["below_minimum_count"], 1)

    def test_expected_output_presence_is_counted_separately_from_trust(self):
        with_outputs = self.cases(count=12, expected="42")
        blank = self.cases(count=12, expected="")
        self.question("filled", cases=with_outputs)
        self.question("empty", cases=blank)

        counts, _d, _c, _e = census.hidden_test_census()
        self.assertEqual(counts["cases_with_expected_output"], 12)
        self.assertEqual(counts["cases_without_expected_output"], 12)

    def test_missing_boilerplate_is_detected(self):
        self.question("nolang", boilerplate={})
        self.question("unknown", boilerplate={"brainfuck": "x"})
        counts, _d, _c, _e = census.hidden_test_census()
        self.assertEqual(counts["no_boilerplate"], 1)
        self.assertEqual(counts["boilerplate_without_known_language"], 1)


class ContradictionTests(CensusTestCase):

    def test_clean_bank_has_no_contradictions(self):
        self.question("ok")
        counts = census.contradiction_counts()
        self.assertEqual(counts["draft_oracle_verified"], 0)

    def test_verified_without_reference_is_flagged(self):
        self.question("v", trust=Question.TRUST_ORACLE_VERIFIED)
        counts = census.contradiction_counts()
        self.assertEqual(counts["oracle_verified_without_active_reference"], 1)
        self.assertEqual(counts["oracle_verified_without_approval"], 1)

    def test_frozen_eligibility_on_a_demoted_question_is_counted_not_flagged(self):
        """
        `adaptive_eligible` is frozen at submission time (P2.7c), so an
        eligible submission against a now-unverified question is legitimate
        history — counted, never reported as an error.
        """
        question = self.question("q")
        learner = self.User.objects.create_user(
            username="l", email="l@t.test", password="Pv#2026xyz")
        CodeSubmission.objects.create(
            user=learner, question=question, language="python", code="x",
            status="accepted", adaptive_eligible=True)

        counts = census.contradiction_counts()
        self.assertEqual(
            counts["eligible_submissions_on_now_unverified_questions"], 1)


class ReferenceAndProvenanceTests(CensusTestCase):

    def test_zero_references_is_stated_explicitly(self):
        counts = census.reference_counts()
        self.assertEqual(counts["total"], 0)
        self.assertIn("no ReferenceSolution rows", counts["note"])

    def test_reference_lifecycle_is_reported(self):
        question = self.question("q")
        approved_reference(question, approver=self.operator)
        counts = census.reference_counts()
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["approved_and_active"], 1)
        self.assertEqual(counts["questions_with_active_reference"], 1)

    def test_inactive_reference_is_not_counted_as_canonical(self):
        question = self.question("q")
        approved_reference(question, approver=self.operator, active=False)
        counts = census.reference_counts()
        self.assertEqual(counts["active"], 0)
        self.assertEqual(counts["inactive"], 1)

    def test_zero_provenance_means_every_answer_is_unprovenanced(self):
        self.question("legacy", cases=self.cases(count=20))
        counts = census.provenance_counts()
        self.assertEqual(counts["oracle_execution_rows"], 0)
        self.assertEqual(counts["questions_with_authoritative_provenance"], 0)

    def test_provenance_is_attributed_per_question(self):
        from groups import provenance
        question = self.question("q")
        reference = approved_reference(question, approver=self.operator)
        reference.refresh_from_db()
        provenance.record_execution(
            question=question, reference=reference, stdin="1\n",
            produced_output="1", status=OracleExecution.STATUS_SUCCESS,
            execution_contract_version="v1")

        counts = census.provenance_counts()
        self.assertEqual(counts["oracle_execution_rows"], 1)
        self.assertEqual(counts["questions_with_any_provenance"], 1)
        self.assertEqual(counts["questions_with_authoritative_provenance"], 0)

    def test_zero_approvals_is_stated_explicitly(self):
        counts = census.approval_counts()
        self.assertEqual(counts["total"], 0)
        self.assertIn("no question has been human-approved", counts["note"])

    def test_approval_staleness_is_not_guessed(self):
        question = self.question("q")
        reference = approved_reference(question, approver=self.operator)
        reference.refresh_from_db()
        QuestionApproval.objects.create(
            question=question, reference=reference,
            reference_source_hash=reference.source_hash,
            artifact_digest="a" * 64, approved_by=self.operator,
            approved_at=question.__class__.objects.first().created_at
            if hasattr(question, "created_at") else __import__(
                "django.utils.timezone", fromlist=["now"]).now())

        counts = census.approval_counts()
        self.assertEqual(counts["total"], 1)
        self.assertIn("NOT_EVALUATED", counts["staleness"])


class BlastRadiusTests(CensusTestCase):

    def test_each_question_lands_in_exactly_one_class(self):
        self.question("blocked", status=Question.STATUS_BLOCKED)
        self.question("notests", cases=[])
        self.question("thin", cases=self.cases(count=3))
        self.question("unverified")

        classes = census.reseed_blast_radius()
        self.assertEqual(sum(classes.values()), Question.objects.count())

    def test_blocked_questions_are_not_reseed_candidates(self):
        self.question("blocked", status=Question.STATUS_BLOCKED, cases=[])
        classes = census.reseed_blast_radius()
        self.assertEqual(classes.get(census.BLOCKED), 1)
        self.assertIsNone(classes.get(census.GENERATE_TESTS))

    def test_missing_tests_dominate_oracle_need(self):
        """Severity ordering: a question with no tests needs tests first."""
        self.question("empty", cases=[])
        classes = census.reseed_blast_radius()
        self.assertEqual(classes.get(census.GENERATE_TESTS), 1)
        self.assertIsNone(classes.get(census.ORACLE_REQUIRED))

    def test_a_well_formed_unverified_question_needs_the_oracle(self):
        self.question("ok", cases=self.cases(count=12))
        classes = census.reseed_blast_radius()
        self.assertEqual(classes.get(census.ORACLE_REQUIRED), 1)

    def test_a_fully_trusted_question_is_safe(self):
        self.question("done", cases=self.cases(count=12),
                      trust=Question.TRUST_ORACLE_VERIFIED)
        classes = census.reseed_blast_radius()
        self.assertEqual(classes.get(census.SAFE), 1)


# ═════════════════════════════════════════════════════════════
# Command
# ═════════════════════════════════════════════════════════════

class CommandTests(CensusTestCase):

    def run_census(self, **kwargs):
        buffer = StringIO()
        call_command("question_bank_census", "--allow-non-production",
                     "--allow-write-role", "--json", stdout=buffer, **kwargs)
        return json.loads(buffer.getvalue())

    def test_refuses_to_run_against_a_local_database_by_default(self):
        with self.assertRaises(CommandError) as caught:
            call_command("question_bank_census")
        message = str(caught.exception)
        self.assertIn("CONNECTION GATE FAILED", message)
        self.assertIn("no counts are reported", message)

    def test_refuses_a_writable_role_even_when_non_production_is_allowed(self):
        with self.assertRaises(CommandError) as caught:
            call_command("question_bank_census", "--allow-non-production")
        self.assertIn("WRITE privileges", str(caught.exception))

    def test_report_is_stamped_non_production(self):
        payload = self.run_census()
        self.assertFalse(payload["database_identity"]["is_production"])
        self.assertTrue(any("NON-PRODUCTION" in w
                            for w in payload["warnings"]))

    def test_report_contains_every_required_section(self):
        payload = self.run_census()
        for key in ("generated_at", "database_identity", "schema_state",
                    "question_counts", "trust_counts",
                    "adaptive_eligibility_counts", "hidden_test_counts",
                    "duplicate_counts", "gradability_counts",
                    "reference_counts", "provenance_counts",
                    "approval_counts", "contradiction_counts",
                    "reseed_candidates", "safe_to_leave_untouched",
                    "blockers", "warnings", "report_hash"):
            self.assertIn(key, payload)

    def test_report_never_contains_a_password(self):
        self.question("q")
        payload = self.run_census()
        serialised = json.dumps(payload)
        from django.conf import settings
        secret = settings.DATABASES["default"].get("PASSWORD")
        if secret:
            self.assertNotIn(secret, serialised)
        self.assertNotIn("PASSWORD", serialised)

    def test_the_duplicate_divergence_is_always_reported(self):
        payload = self.run_census()
        self.assertTrue(any("DUPLICATE DEFINITION DIVERGENCE" in w
                            for w in payload["warnings"]))

    def test_report_hash_is_stable_across_runs(self):
        self.question("q")
        self.assertEqual(self.run_census()["report_hash"],
                         self.run_census()["report_hash"])

    def test_report_hash_changes_when_the_bank_changes(self):
        self.question("a")
        before = self.run_census()["report_hash"]
        self.question("b")
        self.assertNotEqual(self.run_census()["report_hash"], before)

    def test_the_census_writes_nothing(self):
        question = self.question("q")
        before = (Question.objects.count(),
                  list(Question.objects.values_list("trust_state", flat=True)),
                  list(Question.objects.values_list("hidden_test_cases",
                                                    flat=True)),
                  CodeSubmission.objects.count())
        self.run_census()
        question.refresh_from_db()
        self.assertEqual(
            (Question.objects.count(),
             list(Question.objects.values_list("trust_state", flat=True)),
             list(Question.objects.values_list("hidden_test_cases", flat=True)),
             CodeSubmission.objects.count()), before)

    def test_empty_database_produces_a_report_not_a_crash(self):
        payload = self.run_census()
        self.assertEqual(payload["question_counts"]["total"], 0)
        self.assertTrue(payload["blockers"])

    def test_zero_provenance_is_reported_as_a_blocker(self):
        self.question("legacy", cases=self.cases(count=20))
        payload = self.run_census()
        self.assertTrue(any("LEGACY/UNPROVENANCED" in b
                            for b in payload["blockers"]))

    def test_command_has_no_write_flags(self):
        from groups.management.commands.question_bank_census import Command
        parser = Command().create_parser("manage.py", "question_bank_census")
        flags = {action.dest for action in parser._actions}
        for forbidden in ("apply", "fix", "repair", "reseed", "promote",
                          "generate", "migrate"):
            self.assertNotIn(forbidden, flags)


# ═════════════════════════════════════════════════════════════
# Structural guards
# ═════════════════════════════════════════════════════════════

FORBIDDEN = {"expected_output", "hidden_test_cases", "trust_state", "status",
             "adaptive_eligible", "content", "is_active", "review_state"}


def _writes(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assigned, called = set(), set()

    def names(node):
        if isinstance(node, ast.Attribute):
            return [node.attr]
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return [key.value]
            return []
        if isinstance(node, (ast.Tuple, ast.List)):
            return [n for e in node.elts for n in names(e)]
        return []

    def persistence(func):
        if not isinstance(func, ast.Attribute):
            return None
        if func.attr in {"save", "delete"}:
            return func.attr
        if func.attr in {"create", "update", "bulk_create", "bulk_update",
                         "get_or_create", "update_or_create"}:
            receiver = func.value
            if isinstance(receiver, ast.Attribute) and receiver.attr == "objects":
                return f"objects.{func.attr}"
        return None

    def visit(node, inside):
        entering = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        scope = inside or entering
        if scope:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                assigned.update(names(target))
            if isinstance(node, ast.Call):
                found = persistence(node.func)
                if found:
                    called.add(found)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, False)
    return assigned, called


class StructuralGuardTests(TestCase):

    def test_census_modules_perform_no_writes(self):
        from groups.management.commands import question_bank_census
        for module in (census, census_gates, question_bank_census):
            assigned, called = _writes(inspect.getfile(module))
            self.assertEqual(assigned & FORBIDDEN, set(),
                             f"{module.__name__} assigns protected state")
            self.assertEqual(called, set(),
                             f"{module.__name__} performs ORM persistence")

    def test_census_does_not_import_any_writer(self):
        """
        Checked as IMPORTS, not as text.

        A first version grepped for "reseed_questions" and failed on
        `census._normalized_duplicates`' docstring, which names it while
        explaining why the two duplicate definitions diverge. Naming a
        dangerous module in order to explain it is the opposite of depending
        on it — and this is now the sixth phase in which a text-matching guard
        has been defeated by the prose describing the thing it forbids.
        """
        forbidden_modules = {
            "reseed_questions", "question_promote", "question_approve",
            "provenance", "oracle_pipeline", "glicko_history",
        }

        from groups.management.commands import question_bank_census
        for module in (census, census_gates, question_bank_census):
            tree = ast.parse(pathlib.Path(
                inspect.getfile(module)).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.update(alias.name.split("."))
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.update(node.module.split("."))
                    for alias in node.names:
                        imported.add(alias.name)
            self.assertEqual(
                imported & forbidden_modules, set(),
                f"{module.__name__} imports a module that can write")

    def test_guard_catches_a_real_write(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write("def f(q):\n    q.trust_state = 'X'\n    q.save()\n")
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        assigned, called = _writes(path)
        self.assertIn("trust_state", assigned)
        self.assertIn("save", called)
