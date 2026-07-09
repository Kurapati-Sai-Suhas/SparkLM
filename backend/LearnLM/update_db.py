from groups.models import Question

q = Question.objects.get(id=119)
if q:
    q.content = """Given an integer `rowIndex`, return the **`rowIndex`-th (0-indexed)** row of the Pascal's triangle.

In Pascal's triangle, each number is the sum of the two numbers directly above it.

**Example 1:**
```
Input: rowIndex = 3
Output: [1,3,3,1]
```

**Example 2:**
```
Input: rowIndex = 0
Output: [1]
```

**Example 3:**
```
Input: rowIndex = 1
Output: [1,1]
```

**Constraints:**
* `0 <= rowIndex <= 33`"""
    q.starter_code = """class Solution:
    def getRow(self, rowIndex: int) -> list[int]:
        # TODO: Implement your solution for Pascal's Triangle II
        pass"""
    q.save()
    print('Updated question:', q.title)
else:
    print('Question not found.')
