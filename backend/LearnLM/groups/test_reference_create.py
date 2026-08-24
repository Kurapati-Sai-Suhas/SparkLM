"""
Reference authoring (M2 P2.7 pilot).

The command creates a DRAFT and nothing else. Most of this suite exists to
prove the "nothing else" half: it must not hash, submit, approve, activate, or
touch the question it belongs to — and it must never echo the answer key.
"""

import ast
import inspect
import pathlib
import tempfile
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from groups.models import CodingPortal, Question, ReferenceSolution, Topic

SOURCE = "import sys\nprint(sys.stdin.read().strip().count('01') <= 1)\n"


class ReferenceCreateTestCase(TestCase):

    def setUp(self):
        User = get_user_model()
        self.operator = User.objects.create_user(
            username="author", email="a@t.test", password="Pv#2026xyz",
            is_staff=True)
        portal = CodingPortal.objects.create(name="RC Portal")
        self.topic = Topic.objects.create(name="RCTopic",
                                          structure_type="flat", portal=portal)
        self.question = self._question()

    def _question(self, title="Binary segments", contract="v1"):
        return Question.objects.create(
            title=title, content="c", topic=self.topic, base_difficulty=1200.0,
            hidden_test_cases=[{"stdin": f"{n}\n", "expected_output": str(n)}
                               for n in range(10)],
            boilerplate_code={"python": "def solve(): ..."},
            hidden_wrapper_code={}, execution_contract_version=contract)

    def source_file(self, text=SOURCE, *, encoding="utf-8", raw=None):
        handle = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        handle.write(raw if raw is not None else text.encode(encoding))
        handle.close()
        self.addCleanup(
            lambda: pathlib.Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def create(self, **overrides):
        options = {
            "question": str(self.question.pk),
            "language": "python",
            "source_file": self.source_file(),
            "operator": self.operator.username,
        }
        options.update(overrides)
        buffer = StringIO()
        args = ["reference_create",
                "--question", options["question"],
                "--language", options["language"],
                "--source-file", options["source_file"],
                "--operator", options["operator"]]
        if overrides.get("confirm", True):
            args.append("--confirm")
        call_command(*args, stdout=buffer)
        return buffer.getvalue()


# ═════════════════════════════════════════════════════════════
# Happy path — and everything it must NOT do
# ═════════════════════════════════════════════════════════════

class CreationTests(ReferenceCreateTestCase):

    def test_creates_a_draft_inactive_reference(self):
        self.create()
        reference = ReferenceSolution.objects.get(question=self.question)
        self.assertEqual(reference.review_state, ReferenceSolution.REVIEW_DRAFT)
        self.assertFalse(reference.is_active)
        self.assertEqual(reference.language, "python")
        self.assertEqual(reference.source_code, SOURCE)

    def test_source_hash_remains_null(self):
        """
        The hash is APPROVAL PROVENANCE, not a property of the text. Setting it
        here would assert a review that never happened — and the
        `reference_approval_provenance` CHECK forbids it while not APPROVED.
        """
        self.create()
        self.assertIn(
            ReferenceSolution.objects.get(question=self.question).source_hash,
            (None, ""))

    def test_approval_provenance_remains_null(self):
        self.create()
        reference = ReferenceSolution.objects.get(question=self.question)
        self.assertIsNone(reference.approved_by_id)
        self.assertIsNone(reference.approved_at)

    def test_the_created_row_is_not_canonical(self):
        """A fresh reference must not become an oracle by existing."""
        from groups.oracle import canonical_reference
        self.create()
        self.assertIsNone(canonical_reference(self.question))

    def test_source_is_stored_byte_exact(self):
        text = "def f():\n\treturn '  spaced  '\n# trailing\n"
        self.create(source_file=self.source_file(text))
        self.assertEqual(
            ReferenceSolution.objects.get(question=self.question).source_code,
            text)

    def test_language_is_normalised(self):
        self.create(language="PYTHON")
        self.assertEqual(
            ReferenceSolution.objects.get(question=self.question).language,
            "python")


# ═════════════════════════════════════════════════════════════
# Validation
# ═════════════════════════════════════════════════════════════

class ValidationTests(ReferenceCreateTestCase):

    def assert_refused(self, fragment, **overrides):
        with self.assertRaises(CommandError) as caught:
            self.create(**overrides)
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(ReferenceSolution.objects.count(), 0,
                         "a row was written despite the refusal")

    def test_unknown_question_is_refused(self):
        self.assert_refused("no such question", question="99999999")

    def test_unknown_language_is_refused(self):
        self.assert_refused("unknown language", language="brainfuck")

    def test_unknown_execution_contract_is_refused(self):
        """
        A reference is written against a specific harness. If the question
        declares one this codebase cannot execute, the reference could never be
        validated and must not be stored.
        """
        self.question.execution_contract_version = "v99"
        self.question.save(update_fields=["execution_contract_version"])
        self.assert_refused("execution contract")

    def test_missing_source_file_is_refused(self):
        self.assert_refused("no such source file",
                            source_file="/nonexistent/solution.py")

    def test_directory_as_source_file_is_refused(self):
        self.assert_refused("no such source file",
                            source_file=tempfile.mkdtemp())

    def test_non_utf8_source_is_refused_not_repaired(self):
        """
        Replacing an undecodable byte would silently alter the answer key, so
        the file is refused rather than coerced.
        """
        self.assert_refused("not valid UTF-8",
                            source_file=self.source_file(raw=b"\xff\xfe bad"))

    def test_empty_source_is_refused(self):
        self.assert_refused("empty or contains only whitespace",
                            source_file=self.source_file(""))

    def test_whitespace_only_source_is_refused(self):
        self.assert_refused("empty or contains only whitespace",
                            source_file=self.source_file("   \n\t\n  "))

    def test_non_staff_operator_is_refused(self):
        get_user_model().objects.create_user(
            username="learner", email="l@t.test", password="Pv#2026xyz")
        self.assert_refused("not staff", operator="learner")

    def test_inactive_operator_is_refused(self):
        self.operator.is_active = False
        self.operator.save(update_fields=["is_active"])
        self.assert_refused("not an active account")

    def test_unknown_operator_is_refused(self):
        self.assert_refused("no such user", operator="ghost")

    def test_confirm_is_required(self):
        self.assert_refused("--confirm", confirm=False)


# ═════════════════════════════════════════════════════════════
# Duplicates
# ═════════════════════════════════════════════════════════════

class DuplicateTests(ReferenceCreateTestCase):

    def test_second_reference_in_the_same_language_is_refused(self):
        """
        Two candidate answer keys for one problem in one language is how
        `canonical_reference()` ends up unable to choose — which turns an
        authoring slip into a silently unverifiable question.
        """
        self.create()
        with self.assertRaises(CommandError) as caught:
            self.create()
        self.assertIn("already has a non-REJECTED", str(caught.exception))
        self.assertEqual(ReferenceSolution.objects.count(), 1)

    def test_a_rejected_reference_does_not_block_a_replacement(self):
        """REJECTED is not live — re-authoring after review is the normal path."""
        self.create()
        reference = ReferenceSolution.objects.get()
        reference.submit_for_review()
        reference.reject()

        self.create()
        self.assertEqual(ReferenceSolution.objects.count(), 2)

    def test_a_second_language_is_allowed(self):
        self.create()
        self.create(language="java")
        self.assertEqual(
            set(ReferenceSolution.objects.values_list("language", flat=True)),
            {"python", "java"})

    def test_an_active_reference_in_another_language_warns_but_allows(self):
        self.create()
        reference = ReferenceSolution.objects.get()
        reference.submit_for_review()
        reference.approve(by=self.operator)
        reference.activate()

        output = self.create(language="java")
        self.assertIn("already has an ACTIVE", output)
        self.assertEqual(ReferenceSolution.objects.count(), 2)

    def test_references_for_different_questions_do_not_collide(self):
        other = self._question(title="Other")
        self.create()
        self.create(question=str(other.pk))
        self.assertEqual(ReferenceSolution.objects.count(), 2)


# ═════════════════════════════════════════════════════════════
# Secrecy
# ═════════════════════════════════════════════════════════════

class SecrecyTests(ReferenceCreateTestCase):

    def test_the_source_is_never_printed(self):
        secret = "print('THE_ANSWER_KEY_SENTINEL')\n"
        output = self.create(source_file=self.source_file(secret))
        self.assertNotIn("THE_ANSWER_KEY_SENTINEL", output)
        self.assertNotIn(secret.strip(), output)

    def test_metadata_is_shown_instead(self):
        output = self.create()
        self.assertIn("source bytes", output)
        self.assertIn("source sha256", output)
        self.assertIn("DRAFT", output)

    def test_there_is_no_inline_source_argument(self):
        """
        An answer key passed as an argument lands in shell history and the
        process table. The flag must not exist at all.
        """
        from groups.management.commands.reference_create import Command
        parser = Command().create_parser("manage.py", "reference_create")
        flags = {action.dest for action in parser._actions}
        self.assertIn("source_file", flags)
        for forbidden in ("source", "code", "solution", "inline"):
            self.assertNotIn(forbidden, flags)

    def test_no_lifecycle_shortcut_flags_exist(self):
        from groups.management.commands.reference_create import Command
        parser = Command().create_parser("manage.py", "reference_create")
        flags = {action.dest for action in parser._actions}
        for forbidden in ("approve", "activate", "submit", "force",
                          "review_state", "is_active"):
            self.assertNotIn(forbidden, flags)


# ═════════════════════════════════════════════════════════════
# The question must be untouched
# ═════════════════════════════════════════════════════════════

class QuestionUntouchedTests(ReferenceCreateTestCase):

    def snapshot(self):
        question = Question.objects.get(pk=self.question.pk)
        return {
            "content": question.content,
            "hidden_test_cases": question.hidden_test_cases,
            "boilerplate_code": question.boilerplate_code,
            "hidden_wrapper_code": question.hidden_wrapper_code,
            "status": question.status,
            "trust_state": question.trust_state,
            "execution_contract_version": question.execution_contract_version,
            "base_difficulty": question.base_difficulty,
        }

    def test_the_question_is_completely_unchanged(self):
        before = self.snapshot()
        self.create()
        self.assertEqual(self.snapshot(), before)

    def test_hidden_tests_and_expected_outputs_are_unchanged(self):
        before = Question.objects.get(pk=self.question.pk).hidden_test_cases
        self.create()
        after = Question.objects.get(pk=self.question.pk).hidden_test_cases
        self.assertEqual(after, before)
        self.assertEqual([c["expected_output"] for c in after],
                         [c["expected_output"] for c in before])

    def test_no_other_trust_row_is_created(self):
        from groups.models import (
            GlickoSnapshot, OracleExecution, QuestionApproval,
        )
        self.create()
        self.assertEqual(OracleExecution.objects.count(), 0)
        self.assertEqual(QuestionApproval.objects.count(), 0)
        self.assertEqual(GlickoSnapshot.objects.count(), 0)


# ═════════════════════════════════════════════════════════════
# Structural guards
# ═════════════════════════════════════════════════════════════

FORBIDDEN_ASSIGNMENTS = {
    "review_state", "approved_by", "approved_at", "source_hash", "is_active",
    "trust_state", "status", "hidden_test_cases", "expected_output", "content",
}
FORBIDDEN_IMPORTS = {"oracle_pipeline", "provenance", "requests",
                     "question_promote", "question_approve"}


def _analyse(path):
    """Assignments inside function bodies, plus every imported module name."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assigned, imported = set(), set()

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
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(node.module.split("."))
            for alias in node.names:
                imported.add(alias.name)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, False)
    return assigned, imported


class StructuralGuardTests(TestCase):

    def module_path(self):
        from groups.management.commands import reference_create
        return inspect.getfile(reference_create)

    def test_no_lifecycle_or_grading_field_is_assigned(self):
        """
        AST-based, and scoped to function bodies. The command must let the
        model defaults produce DRAFT/inactive/NULL rather than asserting them.
        """
        assigned, _ = _analyse(self.module_path())
        self.assertEqual(assigned & FORBIDDEN_ASSIGNMENTS, set())

    def test_no_execution_or_network_module_is_imported(self):
        _, imported = _analyse(self.module_path())
        self.assertEqual(imported & FORBIDDEN_IMPORTS, set())

    def test_the_guard_detects_a_real_violation(self):
        """A guard that cannot fail proves nothing."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write("import requests\n"
                         "def f(r):\n    r.source_hash = 'x'\n")
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        assigned, imported = _analyse(path)
        self.assertIn("source_hash", assigned)
        self.assertIn("requests", imported)

    def test_the_guard_is_not_fooled_by_a_docstring(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write('"""Sets source_hash and imports requests."""\n'
                         "def f(r):\n    return r\n")
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        assigned, imported = _analyse(path)
        self.assertNotIn("source_hash", assigned)
        self.assertNotIn("requests", imported)


# ═════════════════════════════════════════════════════════════
# Test isolation
# ═════════════════════════════════════════════════════════════

class TestIsolationTests(TestCase):

    def test_the_test_database_is_local(self):
        """
        The suite must never reach production. Asserted here as well as in
        conftest, because this module writes ReferenceSolution rows — the
        exact thing that must not happen against Neon.
        """
        from django.conf import settings
        host = settings.DATABASES["default"]["HOST"]
        self.assertIn(host, ("127.0.0.1", "::1", "localhost", ""))
        self.assertNotIn("neon.tech", str(host))

    def test_the_isolation_plugin_is_registered(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        config = (root / "pytest.ini").read_text(encoding="utf-8")
        self.assertIn("-p sparklm_test_isolation", config)
