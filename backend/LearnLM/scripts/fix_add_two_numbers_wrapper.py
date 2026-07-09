import os
import django
import sys

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

from groups.models import Question

def fix_wrappers():
    print("Fixing Add Two Numbers wrappers...")
    q = Question.objects.filter(title__icontains="Add Two Numbers").first()
    if not q:
        return
    
    java_wrapper = """import java.util.*;

class ListNode {
    int val;
    ListNode next;
    ListNode() {}
    ListNode(int val) { this.val = val; }
    ListNode(int val, ListNode next) { this.val = val; this.next = next; }
}

{user_code}

public class Main {
    public static ListNode buildList(String s) {
        if (s == null || s.trim().isEmpty()) return null;
        String[] parts = s.trim().split("\\\\s+");
        ListNode dummy = new ListNode(0);
        ListNode curr = dummy;
        for (String p : parts) {
            curr.next = new ListNode(Integer.parseInt(p));
            curr = curr.next;
        }
        return dummy.next;
    }
    
    public static void printList(ListNode head) {
        List<String> res = new ArrayList<>();
        while (head != null) {
            res.add(String.valueOf(head.val));
            head = head.next;
        }
        System.out.println(String.join(" ", res));
    }

    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        if (!scanner.hasNextLine()) return;
        String line1 = scanner.nextLine().trim();
        if (!scanner.hasNextLine()) return;
        String line2 = scanner.nextLine().trim();
        
        ListNode l1 = buildList(line1);
        ListNode l2 = buildList(line2);
        
        Solution sol = new Solution();
        ListNode res = sol.addTwoNumbers(l1, l2);
        printList(res);
    }
}
"""

    python_wrapper = """import sys
import json

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

{user_code}

def buildList(nums):
    dummy = ListNode(0)
    curr = dummy
    for n in nums:
        curr.next = ListNode(int(n))
        curr = curr.next
    return dummy.next

def printList(head):
    res = []
    while head:
        res.append(str(head.val))
        head = head.next
    print(" ".join(res))

if __name__ == '__main__':
    lines = sys.stdin.read().strip().split('\\n')
    if len(lines) >= 2:
        l1 = buildList(lines[0].split())
        l2 = buildList(lines[1].split())
        sol = Solution()
        res = sol.addTwoNumbers(l1, l2)
        printList(res)
"""

    q.hidden_wrapper_code = {
        "java": java_wrapper,
        "python": python_wrapper
    }
    
    # Also fix test cases to match the wrapper expectations
    # 342 (2 4 3) + 465 (5 6 4) = 807 (7 0 8)
    q.hidden_test_cases = [
        {"stdin": "2 4 3\\n5 6 4", "expected_output": "7 0 8"},
        {"stdin": "0\\n0", "expected_output": "0"},
        {"stdin": "9 9 9 9 9 9 9\\n9 9 9 9", "expected_output": "8 9 9 9 0 0 0 1"}
    ]
    
    q.save()
    print("Fixed!")

if __name__ == "__main__":
    fix_wrappers()
