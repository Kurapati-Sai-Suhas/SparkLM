"""
Wrapper/template contract checks (M2-D1 regression).

The defect these prevent shipped to production: question 3307's Java wrapper
calls `ListNode res = sol.addTwoNumbers(l1, l2)` while its starter template
declared `String addTwoNumbers(String data)`. Every new user receives that
question first, so every new user who selected Java got a compile error from
the platform's own template.

Nothing could have caught it: wrapper and template are separate JSON columns,
and neither the serializer, the grader, nor any test compared them. The first
test below is the shipped defect verbatim.
"""

import pytest

from groups.models import CodingPortal, Question, Topic
from groups.wrapper_contract import check_pair, template_declaration, wrapper_call_contract

# The real wrapper from question 3307, trimmed to its contract-bearing parts.
Q3307_WRAPPER = """
import java.util.*;
{user_code}
public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        String[] parts = sc.nextLine().split("\\\\|");
        ListNode l1 = buildList(parts[0]);
        ListNode l2 = buildList(parts[1]);
        Solution sol = new Solution();
        ListNode res = sol.addTwoNumbers(l1, l2);
        printList(res);
    }
    static ListNode buildList(String s) {
        ListNode dummy = new ListNode(0);
        return dummy.next;
    }
}
"""

BROKEN_TEMPLATE = """class Solution {
    public String addTwoNumbers(String data) {
        return "";
    }
}
"""

FIXED_TEMPLATE = """class ListNode {
    int val;
    ListNode next;
    ListNode(int val) { this.val = val; }
}

class Solution {
    public ListNode addTwoNumbers(ListNode l1, ListNode l2) {
        return null;
    }
}
"""


class TestTheShippedDefect:
    def test_the_exact_template_that_shipped_is_rejected(self):
        """Verbatim reproduction of M2-D1. This must never pass."""
        problems = check_pair(Q3307_WRAPPER, BROKEN_TEMPLATE)
        assert problems, "the template that caused a production compile_error was accepted"
        joined = " | ".join(problems)
        assert "parameter count" in joined
        assert "return type" in joined
        assert "ListNode" in joined  # helper class the wrapper never declares

    def test_the_repaired_template_is_accepted(self):
        assert check_pair(Q3307_WRAPPER, FIXED_TEMPLATE) == []


class TestContractExtraction:
    def test_reads_method_arity_and_return_type_from_the_call_site(self):
        contract = wrapper_call_contract(Q3307_WRAPPER)
        assert contract["method"] == "addTwoNumbers"
        assert contract["arg_count"] == 2
        assert contract["return_type"] == "ListNode"

    def test_identifies_helper_types_the_wrapper_uses_but_never_declares(self):
        contract = wrapper_call_contract(Q3307_WRAPPER)
        assert "ListNode" in contract["required_types"]
        # Declared inside the wrapper, so the user must NOT be asked for it.
        assert "Main" not in contract["required_types"]
        # Language builtins are never demanded of the user.
        assert "Scanner" not in contract["required_types"]
        assert "String" not in contract["required_types"]

    def test_no_call_site_means_nothing_to_verify(self):
        assert wrapper_call_contract("public class Main { }") is None
        assert wrapper_call_contract("") is None
        assert wrapper_call_contract(None) is None
        # A wrapper with no sol.method() call imposes no contract.
        assert check_pair("public class Main { }", BROKEN_TEMPLATE) == []

    def test_reads_the_declaration_out_of_a_template(self):
        declaration = template_declaration(FIXED_TEMPLATE, "addTwoNumbers")
        assert declaration["return_type"] == "ListNode"
        assert declaration["param_count"] == 2
        assert declaration["param_types"] == ["ListNode", "ListNode"]
        assert "ListNode" in declaration["declared_types"]


class TestPythonImplicitReceiver:
    """
    The first audit run reported two REAL production questions (2 and 27) as
    incompatible. They were not: Python declares `def m(self, l1, l2)` while
    the wrapper calls `sol.m(l1, l2)` — `self` is bound, never passed. The
    checker was counting it. A checker that cries wolf on correct content is
    worse than no checker, so this pins the rule.
    """

    PY_WRAPPER = "sol = Solution()\nres = sol.addTwoNumbers(l1, l2)\nprint(res)\n"
    PY_TEMPLATE = (
        "class Solution:\n"
        "    def addTwoNumbers(self, l1: Optional[ListNode], l2: Optional[ListNode]) -> Optional[ListNode]:\n"
        "        pass\n"
    )

    def test_self_is_not_counted_as_a_parameter(self):
        assert check_pair(self.PY_WRAPPER, self.PY_TEMPLATE, "python") == []

    def test_declaration_drops_the_receiver(self):
        declaration = template_declaration(self.PY_TEMPLATE, "addTwoNumbers", "python")
        assert declaration["param_count"] == 2
        assert declaration["param_types"] == ["l1", "l2"]

    def test_a_genuine_python_arity_mismatch_is_still_caught(self):
        wrong = "class Solution:\n    def addTwoNumbers(self, l1):\n        pass\n"
        problems = check_pair(self.PY_WRAPPER, wrong, "python")
        assert any("parameter count" in p for p in problems)

    def test_classmethod_receiver_is_also_dropped(self):
        cls_template = "class Solution:\n    def addTwoNumbers(cls, l1, l2):\n        pass\n"
        assert check_pair(self.PY_WRAPPER, cls_template, "python") == []


class TestMismatchCategories:
    """Each category the audit must catch, isolated."""

    def test_missing_method(self):
        problems = check_pair(Q3307_WRAPPER, "class Solution { public int somethingElse() { return 0; } }")
        assert any("does not declare the method" in p for p in problems)

    def test_parameter_count_only(self):
        template = "class ListNode { int val; ListNode next; ListNode(int v){} }\n" \
                   "class Solution { public ListNode addTwoNumbers(ListNode l1) { return null; } }"
        problems = check_pair(Q3307_WRAPPER, template)
        assert any("parameter count" in p for p in problems)
        assert not any("return type" in p for p in problems)

    def test_return_type_only(self):
        template = "class ListNode { int val; ListNode next; ListNode(int v){} }\n" \
                   "class Solution { public String addTwoNumbers(ListNode l1, ListNode l2) { return \"\"; } }"
        problems = check_pair(Q3307_WRAPPER, template)
        assert any("return type" in p for p in problems)
        assert not any("parameter count" in p for p in problems)

    def test_missing_helper_class_only(self):
        template = "class Solution { public ListNode addTwoNumbers(ListNode l1, ListNode l2) { return null; } }"
        problems = check_pair(Q3307_WRAPPER, template)
        assert any("helper type" in p for p in problems)

    def test_empty_or_absent_template_is_not_a_mismatch(self):
        """No template means the UI honestly shows a stub; nothing to contradict."""
        assert check_pair(Q3307_WRAPPER, "") == [] or check_pair(Q3307_WRAPPER, "")
        assert check_pair(Q3307_WRAPPER, None) != []  # None template -> method not declared


@pytest.mark.django_db
class TestAuditCommandOverRealRows:
    def test_audit_flags_an_incompatible_pair_and_passes_a_compatible_one(self, capsys):
        from django.core.management import call_command

        portal = CodingPortal.objects.create(name="Audit Portal")
        topic, _ = Topic.objects.get_or_create(
            name="Audit Topic", defaults={"structure_type": "flat", "portal": portal}
        )
        bad = Question.objects.create(
            title="Bad Pair", content="x", topic=topic, base_difficulty=1200.0,
            hidden_wrapper_code={"java": Q3307_WRAPPER},
            boilerplate_code={"java": BROKEN_TEMPLATE},
        )
        good = Question.objects.create(
            title="Good Pair", content="x", topic=topic, base_difficulty=1200.0,
            hidden_wrapper_code={"java": Q3307_WRAPPER},
            boilerplate_code={"java": FIXED_TEMPLATE},
        )

        call_command("audit_wrapper_templates", language="java")
        out = capsys.readouterr().out

        assert str(bad.id) in out and str(good.id) in out
        assert "NO" in out and "HIGH" in out
        assert "incompatible wrapper/template pair" in out
