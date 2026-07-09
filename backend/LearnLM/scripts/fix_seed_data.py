import os
import django
import sys

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

from groups.models import Topic, Question

def clean_topics():
    print("Cleaning Topic names...")
    topics = list(Topic.objects.all())
    count = 0
    for t in topics:
        old_name = t.name
        new_name = old_name.replace("[", "").replace("]", "").replace("'", "").strip()
        if new_name != old_name:
            try:
                t.name = new_name
                t.save()
                count += 1
                print(f"   Renamed: {old_name} -> {new_name}")
            except Exception as e:
                # If there's an integrity error, it means new_name already exists.
                # We should reassign its questions to the existing one and delete t.
                existing_topic = Topic.objects.filter(name=new_name).first()
                if existing_topic:
                    print(f"   Merging duplicate: {old_name} into {new_name}")
                    Question.objects.filter(topic=t).update(topic=existing_topic)
                    t.delete()
    print("Cleaned {} topics.".format(count))

def enrich_questions():
    print("\nEnriching popular Questions...")
    
    # 1. Two Sum
    q_twosum = Question.objects.filter(title__icontains="Two Sum").first()
    if q_twosum:
        q_twosum.content = """<p>Given an array of integers <code>nums</code> and an integer <code>target</code>, return indices of the two numbers such that they add up to <code>target</code>.</p>
<p>You may assume that each input would have exactly one solution, and you may not use the same element twice. You can return the answer in any order.</p>
<br/>
<p><strong>Example 1:</strong></p>
<pre><strong>Input:</strong> nums = [2,7,11,15], target = 9
<strong>Output:</strong> [0,1]
<strong>Explanation:</strong> Because nums[0] + nums[1] == 9, we return [0, 1].
</pre>
<br/>
<p><strong>Example 2:</strong></p>
<pre><strong>Input:</strong> nums = [3,2,4], target = 6
<strong>Output:</strong> [1,2]
</pre>"""
        q_twosum.boilerplate_code = {
            "python": "class Solution:\n    def twoSum(self, nums: list[int], target: int) -> list[int]:\n        pass",
            "java": "class Solution {\n    public int[] twoSum(int[] nums, int target) {\n        return new int[]{};\n    }\n}",
            "cpp": "class Solution {\npublic:\n    vector<int> twoSum(vector<int>& nums, int target) {\n        return {};\n    }\n};"
        }
        q_twosum.save()
        print(f"   Enriched: {q_twosum.title}")

    # 2. Reverse Linked List
    q_rev = Question.objects.filter(title__icontains="Reverse Linked List").first()
    if q_rev:
        q_rev.content = """<p>Given the <code>head</code> of a singly linked list, reverse the list, and return the reversed list.</p>
<br/>
<p><strong>Example 1:</strong></p>
<pre><strong>Input:</strong> head = [1,2,3,4,5]
<strong>Output:</strong> [5,4,3,2,1]
</pre>
<br/>
<p><strong>Example 2:</strong></p>
<pre><strong>Input:</strong> head = [1,2]
<strong>Output:</strong> [2,1]
</pre>"""
        q_rev.boilerplate_code = {
            "python": "# Definition for singly-linked list.\n# class ListNode:\n#     def __init__(self, val=0, next=None):\n#         self.val = val\n#         self.next = next\nclass Solution:\n    def reverseList(self, head: Optional[ListNode]) -> Optional[ListNode]:\n        pass",
            "java": "/**\n * Definition for singly-linked list.\n * public class ListNode {\n *     int val;\n *     ListNode next;\n *     ListNode() {}\n *     ListNode(int val) { this.val = val; }\n *     ListNode(int val, ListNode next) { this.val = val; this.next = next; }\n * }\n */\nclass Solution {\n    public ListNode reverseList(ListNode head) {\n        return null;\n    }\n}"
        }
        q_rev.save()
        print(f"   Enriched: {q_rev.title}")

    # 3. Best Time to Buy and Sell Stock
    q_stock = Question.objects.filter(title__icontains="Best Time to Buy and Sell Stock").first()
    if q_stock:
        q_stock.content = """<p>You are given an array <code>prices</code> where <code>prices[i]</code> is the price of a given stock on the <code>i</code>th day.</p>
<p>You want to maximize your profit by choosing a single day to buy one stock and choosing a different day in the future to sell that stock.</p>
<p>Return the maximum profit you can achieve from this transaction. If you cannot achieve any profit, return <code>0</code>.</p>
<br/>
<p><strong>Example 1:</strong></p>
<pre><strong>Input:</strong> prices = [7,1,5,3,6,4]
<strong>Output:</strong> 5
<strong>Explanation:</strong> Buy on day 2 (price = 1) and sell on day 5 (price = 6), profit = 6-1 = 5.
</pre>
<br/>
<p><strong>Example 2:</strong></p>
<pre><strong>Input:</strong> prices = [7,6,4,3,1]
<strong>Output:</strong> 0
</pre>"""
        q_stock.boilerplate_code = {
            "python": "class Solution:\n    def maxProfit(self, prices: list[int]) -> int:\n        pass",
            "java": "class Solution {\n    public int maxProfit(int[] prices) {\n        return 0;\n    }\n}"
        }
        q_stock.save()
        print(f"   Enriched: {q_stock.title}")

    print("Enrichment complete.")

if __name__ == "__main__":
    clean_topics()
    enrich_questions()
