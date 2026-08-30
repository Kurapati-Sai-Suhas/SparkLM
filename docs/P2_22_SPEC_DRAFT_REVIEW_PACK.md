# P2.22 — Specification drafts, batch 1: operator review pack

**24 candidates. 24 draft specifications.** Every one is a DRAFT written by
the assistant and is **not authoritative and not externally verified**.
Nothing here has been frozen, bound to a question, or used to generate
anything.

**Production writes: 0. Oracle runs: 0. New references: 0. New hidden
suites: 0.**

---

## The source basis, stated once

This is the fact that determines how much weight these drafts can carry.

**The bank stores no problem descriptions.** Every one of the 24 candidates
carries `Question.PLACEHOLDER_MARKER` — *"In this problem, you are tasked
with solving the"* — followed by generic filler and a fabricated example
(*Input: Check hidden test cases in sandbox / Output: Expected optimal
result*). Some rows carry nothing but a bare link. The only project-owned
signal about what a question should ask is:

1. the **title**, and
2. the **boilerplate method name**.

So each draft below is the assistant's own reconstruction of a canonical
task from its title. No sentence is copied from any external problem
statement; nothing was scraped. That also means **the title is the entire
evidence base**, and a title can be ambiguous, can name several different
tasks, or can name a task whose details the assistant does not reliably
know. Where that happened it is recorded as NOT_DRAFTED rather than
guessed.

`author_confidence` is the assistant's confidence that it has recalled the
canonical task correctly. `review_risk` is how much damage a wrong draft
would do downstream. **`LOW` does not mean verified.** It means a wrong
draft would be cheap to catch. Every one of the 24 still requires the
operator to read it.

---

## Scan index

| QID | Title | Difficulty | Topic | Status | Confidence | Risk | Digest |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1947 | Three Divisors | easy (1000) | Math | DRAFTED | high | **LOW** | `5f9f5e2bddfe` |
| 1949 | Minimum Garden Perimeter to Collect Enough Apples | medium (1300) | Math | DRAFTED | medium | **MEDIUM** | `131fc62621cc` |
| 1952 | Delete Characters to Make Fancy String | easy (1000) | String | DRAFTED | high | **LOW** | `4a300d000599` |
| 1955 | Maximum Product of the Length of Two Palindromic Substrings | hard (1600) | String | DRAFTED | medium | **HIGH** | `dbd81359f2f3` |
| 1959 | Find the Longest Valid Obstacle Course at Each Position | hard (1600) | Array | DRAFTED | medium | **MEDIUM** | `09d23a6835bb` |
| 1961 | Binary Searchable Numbers in an Unsorted Array | medium (1300) | Array | NOT_DRAFTED | none | **HIGH** | `37b8ffc59109` |
| 1962 | Number of Strings That Appear as Substrings in Word | easy (1000) | Array | DRAFTED | high | **LOW** | `0f4203448a11` |
| 1971 | Number of Ways to Arrive at Destination | medium (1300) | Dynamic Programming | DRAFTED | medium | **MEDIUM** | `47f423dc26f6` |
| 2013 | Maximize the Confusion of an Exam | medium (1300) | String | DRAFTED | high | **LOW** | `99a8ad0c2e43` |
| 2023 | Stock Price Fluctuation | medium (1300) | Hash Table | STRUCTURALLY_BLOCKED | n/a | **HIGH** | `b7b595a8f421` |
| 2034 | Second Minimum Time to Reach Destination | hard (1600) | Breadth-First Search | DRAFTED | medium | **MEDIUM** | `3f9d5005b0c8` |
| 2035 | Sort Linked List Already Sorted Using Absolute Values | medium (1300) | Linked List | STRUCTURALLY_BLOCKED | n/a | **HIGH** | `d5383c3b37d8` |
| 2051 | Count Vowel Substrings of a String | easy (1000) | Hash Table | DRAFTED | high | **MEDIUM** | `cae512fecc5c` |
| 2066 | Paths in Maze That Lead to Same Room | medium (1300) | Graph | NOT_DRAFTED | none | **HIGH** | `a98d6a7263d4` |
| 2070 | Sum of k-Mirror Numbers | hard (1600) | Math | DRAFTED | medium | **MEDIUM** | `efc185160c7f` |
| 2081 | Find All People With Secret | hard (1600) | Depth-First Search | DRAFTED | high | **MEDIUM** | `4e42dd39a753` |
| 2162 | All Ancestors of a Node in a Directed Acyclic Graph | medium (1300) | Depth-First Search | DRAFTED | high | **LOW** | `0fe6860febe9` |
| 2173 | Minimum Weighted Subgraph With the Required Paths | hard (1600) | Graph | DRAFTED | medium | **HIGH** | `d6b2e5220748` |
| 2217 | Maximum Cost of Trip With K Highways | hard (1600) | Dynamic Programming | NOT_DRAFTED | none | **HIGH** | `12c2a0176e09` |
| 2224 | Design Video Sharing Platform | hard (1600) | Hash Table | STRUCTURALLY_BLOCKED | n/a | **HIGH** | `a13f0712a710` |
| 2415 | Number of Nodes With Value One | medium (1300) | Tree | NOT_DRAFTED | none | **HIGH** | `1688b0e2d14a` |
| 2449 | Maximum XOR of Two Non-Overlapping Subtrees | hard (1600) | Tree | NOT_DRAFTED | none | **HIGH** | `fc3e8563cb78` |
| 3323 | Bitwise AND of Numbers Range | medium (1300) | Bit Manipulation | DRAFTED | high | **LOW** | `f9ba201c0a62` |
| 3343 | Hamming Distance | easy (1000) | Bit Manipulation | DRAFTED | high | **LOW** | `c29a735be715` |

---

## Review records

§32F asks for a table carrying QID / Title / Difficulty / Topics /
Operation / Input / Output / Key constraints / Edge cases / Author
confidence / Source basis / Digest / Decision. Those thirteen fields do not
fit legibly as thirteen markdown columns, so each record is rendered
vertically below. Every record carries exactly those fields, and one
decision box.

Source basis is identical for all 24 — **question title + boilerplate
method name, no stored problem description** — and is stated once above
rather than repeated 24 times.

### q1947 — Three Divisors

| Field | Value |
| --- | --- |
| **QID** | 1947 |
| **Title** | Three Divisors |
| **Difficulty** | easy (1000) |
| **Topic** | Math |
| **Operation** | Decide whether a positive integer has exactly three distinct positive divisors, and report that decision. |
| **Input** | One parameter, n, a positive integer. |
| **Output** | A boolean: true when n has exactly three distinct positive divisors, false otherwise. |
| **Key constraints** | n is between 1 and 10000 inclusive. |
| **Edge cases** | 1 has a single divisor. A prime has two. The count includes 1 and n themselves. |
| **Load-bearing** | The count is of DISTINCT divisors, and the required count is exactly three — neither fewer nor more. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `5f9f5e2bddfe0568a1777107` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1949 — Minimum Garden Perimeter to Collect Enough Apples

| Field | Value |
| --- | --- |
| **QID** | 1949 |
| **Title** | Minimum Garden Perimeter to Collect Enough Apples |
| **Difficulty** | medium (1300) |
| **Topic** | Math |
| **Operation** | A square plot is centred on the origin of an infinite grid. The tree at each integer coordinate holds a number of apples equal to the sum of the absolute values of its coordinates. Find the smallest even perimeter of such a plot whose enclosed trees, including those on the boundary, hold at least the required number of apples, and report that perimeter. |
| **Input** | One parameter, neededApples, a positive integer giving the minimum number of apples to collect. |
| **Output** | A single integer: the smallest qualifying perimeter. |
| **Key constraints** | neededApples is between 1 and 10^15 inclusive. |
| **Edge cases** | The plot is axis-aligned and centred on the origin, so its side length is an even number and its perimeter is a multiple of eight. |
| **Load-bearing** | Apples are counted for every tree inside the plot and on its boundary. The answer is the PERIMETER, not the side length. |
| **Author confidence** | medium |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `131fc62621ccbb584ee0747f` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1952 — Delete Characters to Make Fancy String

| Field | Value |
| --- | --- |
| **QID** | 1952 |
| **Title** | Delete Characters to Make Fancy String |
| **Difficulty** | easy (1000) |
| **Topic** | String |
| **Operation** | Remove the fewest characters from a string so that no character appears three or more times consecutively, and report the resulting string. |
| **Input** | One parameter, s, a string of lowercase English letters. |
| **Output** | The resulting string. |
| **Key constraints** | s holds between 1 and 10^5 characters. |
| **Edge cases** | A run of exactly two identical characters is kept in full. A run of length k above two is reduced to two characters. |
| **Load-bearing** | Only consecutive repetition matters; the same character may appear many times in the result provided no three sit adjacent. Characters are removed, and the order of those that remain is preserved. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `4a300d0005997eb8e8d99c17` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1955 — Maximum Product of the Length of Two Palindromic Substrings

| Field | Value |
| --- | --- |
| **QID** | 1955 |
| **Title** | Maximum Product of the Length of Two Palindromic Substrings |
| **Difficulty** | hard (1600) |
| **Topic** | String |
| **Operation** | Find two non-overlapping palindromic substrings of a string, each of odd length, whose lengths have the largest possible product, and report that product. |
| **Input** | One parameter, s, a string of lowercase English letters. |
| **Output** | A single integer: the largest achievable product of the two lengths. |
| **Key constraints** | s holds between 2 and 10^5 characters. |
| **Edge cases** | A single character is a palindrome of length one, so a product is always available. |
| **Load-bearing** | Both substrings have ODD length, they do not share any position, and the quantity maximised is the product of their lengths. |
| **Author confidence** | medium |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `dbd81359f2f3f4e3ff259028` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1959 — Find the Longest Valid Obstacle Course at Each Position

| Field | Value |
| --- | --- |
| **QID** | 1959 |
| **Title** | Find the Longest Valid Obstacle Course at Each Position |
| **Difficulty** | hard (1600) |
| **Topic** | Array |
| **Operation** | For each position in a sequence of obstacle heights, find the length of the longest course that ends at that position and whose heights are non-decreasing, and report those lengths in order. |
| **Input** | One parameter, obstacles, a list of integers giving the heights. |
| **Output** | A list of integers of the same length as the input, giving the answer for each position in order. |
| **Key constraints** | The list holds between 1 and 10^5 integers, each between 1 and 10^7. |
| **Edge cases** | The course must include the obstacle at its own position, so every answer is at least one. |
| **Load-bearing** | Heights along a course are NON-DECREASING, so equal heights may follow one another. Positions chosen for a course keep their original order but need not be adjacent. |
| **Author confidence** | medium |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `09d23a6835bbd576c671e20c` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1961 — Binary Searchable Numbers in an Unsorted Array

| Field | Value |
| --- | --- |
| **QID** | 1961 |
| **Title** | Binary Searchable Numbers in an Unsorted Array |
| **Difficulty** | medium (1300) |
| **Topic** | Array |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **NOT_DRAFTED** | The behaviour of 'Binary Searchable Numbers in an Unsorted Array' is not known to the author with enough confidence to write a specification that later work would bind to. Sourcing the statement is an operator action. |
| **Author confidence** | none |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `37b8ffc591091b7ca7821b55` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1962 — Number of Strings That Appear as Substrings in Word

| Field | Value |
| --- | --- |
| **QID** | 1962 |
| **Title** | Number of Strings That Appear as Substrings in Word |
| **Difficulty** | easy (1000) |
| **Topic** | Array |
| **Operation** | Count how many strings in a collection appear as a contiguous substring of a given word, and report that count. |
| **Input** | Two parameters. The first, patterns, is a list of strings. The second, word, is a single string. |
| **Output** | A single integer: how many entries of patterns occur as a contiguous substring of word. |
| **Key constraints** | patterns holds between 1 and 100 strings; each string and word hold between 1 and 100 lowercase English letters. |
| **Edge cases** | A pattern equal to word counts. Repeated identical patterns are each counted separately. |
| **Load-bearing** | Occurrence is as a CONTIGUOUS substring, and the quantity reported is the number of qualifying patterns rather than the number of occurrences. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `0f4203448a113ce3c1936cd2` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q1971 — Number of Ways to Arrive at Destination

| Field | Value |
| --- | --- |
| **QID** | 1971 |
| **Title** | Number of Ways to Arrive at Destination |
| **Difficulty** | medium (1300) |
| **Topic** | Dynamic Programming |
| **Operation** | In a weighted undirected graph, count the shortest paths from the first node to the last node, and report that count reduced modulo one thousand million and seven. |
| **Input** | Two parameters. The first, n, is the number of nodes, labelled 0 to n-1. The second, roads, is a list of entries, each giving two node labels and the travel time between them. |
| **Output** | A single integer: the number of shortest paths, reduced modulo 1000000007. |
| **Key constraints** | n is between 1 and 200. Each travel time is a positive integer. At most one connection joins any pair of nodes. |
| **Edge cases** | When the first and last node are the same, one path exists. |
| **Load-bearing** | Only paths of MINIMUM total travel time are counted. The result is reduced modulo 1000000007 rather than reported exactly. |
| **Author confidence** | medium |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `47f423dc26f60b78c36c6f27` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2013 — Maximize the Confusion of an Exam

| Field | Value |
| --- | --- |
| **QID** | 2013 |
| **Title** | Maximize the Confusion of an Exam |
| **Difficulty** | medium (1300) |
| **Topic** | String |
| **Operation** | A string records answers as T and F. Any single character may be changed at most k times in total. Find the longest run of identical characters obtainable, and report its length. |
| **Input** | Two parameters. The first, answerKey, is a string of the characters T and F. The second, k, is the number of changes permitted. |
| **Output** | A single integer: the length of the longest achievable run of identical characters. |
| **Key constraints** | answerKey holds between 1 and 5*10^4 characters. k is between 1 and the length of answerKey. |
| **Edge cases** | When k is at least the length of the string, the whole string can be made uniform. |
| **Load-bearing** | At most k characters are changed IN TOTAL, and the run reported is contiguous. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `99a8ad0c2e43dc93d0b9cb73` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2023 — Stock Price Fluctuation

| Field | Value |
| --- | --- |
| **QID** | 2023 |
| **Title** | Stock Price Fluctuation |
| **Difficulty** | medium (1300) |
| **Topic** | Hash Table |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **STRUCTURALLY_BLOCKED** | 'Stock Price Fluctuation' is a DESIGN problem: it requires a stateful class exposing several public methods that are called in sequence. The execution contract admits exactly one public method and one call per test case, so no specification can make this question runnable under the current harness. Reseeding it needs a contract that can express a call sequence. |
| **Author confidence** | n/a |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `b7b595a8f4216330fcb30857` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2034 — Second Minimum Time to Reach Destination

| Field | Value |
| --- | --- |
| **QID** | 2034 |
| **Title** | Second Minimum Time to Reach Destination |
| **Difficulty** | hard (1600) |
| **Topic** | Breadth-First Search |
| **Operation** | Every edge of an undirected graph takes the same time to cross. Signals at every node alternate between allowing and blocking departure on a fixed repeating cycle, and a traveller arriving while departure is blocked waits until it is allowed. Find the second smallest total time in which the first node can reach the last node, and report it. |
| **Input** | Four parameters: n, the number of nodes labelled 1 to n; edges, a list of node pairs; time, the time to cross one edge; and change, the interval at which the signals switch. |
| **Output** | A single integer: the second smallest achievable total time. |
| **Key constraints** | n is between 2 and 10^4. Times and intervals are positive integers. The graph is connected and holds no self-loops or repeated edges. |
| **Edge cases** | The second smallest time is strictly greater than the smallest; two routes taking equal time count once between them. |
| **Load-bearing** | Departure is permitted only during an allowing interval, so waiting time is part of the total. The value reported is the SECOND smallest distinct total time. |
| **Author confidence** | medium |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `3f9d5005b0c893d84f917cbf` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2035 — Sort Linked List Already Sorted Using Absolute Values

| Field | Value |
| --- | --- |
| **QID** | 2035 |
| **Title** | Sort Linked List Already Sorted Using Absolute Values |
| **Difficulty** | medium (1300) |
| **Topic** | Linked List |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **STRUCTURALLY_BLOCKED** | The input is the head of a linked list. Arguments reach a solution through a JSON envelope of plain values, which cannot carry a node object, and no harness in this project builds a linked list from one. The question is unrunnable under the current contract regardless of its specification. |
| **Author confidence** | n/a |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `d5383c3b37d841ed0f4ea33d` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2051 — Count Vowel Substrings of a String

| Field | Value |
| --- | --- |
| **QID** | 2051 |
| **Title** | Count Vowel Substrings of a String |
| **Difficulty** | easy (1000) |
| **Topic** | Hash Table |
| **Operation** | Count the contiguous substrings of a string that consist only of vowels and contain every one of the five vowels at least once, and report that count. |
| **Input** | One parameter, word, a string of lowercase English letters. |
| **Output** | A single integer: the number of qualifying substrings. |
| **Key constraints** | word holds between 1 and 100 characters. |
| **Edge cases** | A qualifying substring is at least five characters long. Substrings are counted by position, so identical text at different positions counts more than once. |
| **Load-bearing** | Every character of a qualifying substring is a vowel, and all five vowels appear within it. |
| **Author confidence** | high |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `cae512fecc5c0b7d64f07a13` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2066 — Paths in Maze That Lead to Same Room

| Field | Value |
| --- | --- |
| **QID** | 2066 |
| **Title** | Paths in Maze That Lead to Same Room |
| **Difficulty** | medium (1300) |
| **Topic** | Graph |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **NOT_DRAFTED** | The behaviour of 'Paths in Maze That Lead to Same Room' is not known to the author with enough confidence to write a specification safely. Sourcing the statement is an operator action. |
| **Author confidence** | none |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `a98d6a7263d44c1f3ae17b50` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2070 — Sum of k-Mirror Numbers

| Field | Value |
| --- | --- |
| **QID** | 2070 |
| **Title** | Sum of k-Mirror Numbers |
| **Difficulty** | hard (1600) |
| **Topic** | Math |
| **Operation** | A number is mirrored in a base when its digits in that base read identically forwards and backwards. Find the smallest n positive integers that are mirrored both in base ten and in a given base, and report their sum. |
| **Input** | Two parameters. The first, k, is the other base. The second, n, is how many such numbers to collect. |
| **Output** | A single integer: the sum of the n smallest qualifying numbers. |
| **Key constraints** | k is between 2 and 9. n is between 1 and 30. |
| **Edge cases** | Single-digit positive integers are mirrored in every base above them. |
| **Load-bearing** | A qualifying number is mirrored in BOTH bases at once, the numbers collected are the n SMALLEST such, and the value reported is their sum. |
| **Author confidence** | medium |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `efc185160c7f57cb55bf6668` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2081 — Find All People With Secret

| Field | Value |
| --- | --- |
| **QID** | 2081 |
| **Title** | Find All People With Secret |
| **Difficulty** | hard (1600) |
| **Topic** | Depth-First Search |
| **Operation** | A secret starts with person 0 and one other person. People meet in pairs at given times, and anyone who knows the secret at the moment of a meeting shares it with the other attendee. Meetings at the same time all take effect together. Report every person who knows the secret once all meetings have happened. |
| **Input** | Three parameters: n, the number of people labelled 0 to n-1; meetings, a list of entries each giving two people and a time; and firstPerson, the person who is told the secret at the start. |
| **Output** | A list of the people who know the secret, in any order. |
| **Key constraints** | n is between 2 and 10^5. Times are positive integers. A person does not meet themselves. |
| **Edge cases** | Person 0 and firstPerson know the secret before any meeting. Meetings sharing a time propagate the secret among all of them together. |
| **Load-bearing** | Meetings take effect in increasing order of time, and simultaneous meetings are resolved as one group so the secret can pass through several of them at that instant. |
| **Author confidence** | high |
| **Review risk** | MEDIUM |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `4e42dd39a75319e206834937` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2162 — All Ancestors of a Node in a Directed Acyclic Graph

| Field | Value |
| --- | --- |
| **QID** | 2162 |
| **Title** | All Ancestors of a Node in a Directed Acyclic Graph |
| **Difficulty** | medium (1300) |
| **Topic** | Depth-First Search |
| **Operation** | For every node of a directed acyclic graph, find all nodes from which it can be reached, and report them in ascending order. |
| **Input** | Two parameters. The first, n, is the number of nodes labelled 0 to n-1. The second, edges, is a list of directed node pairs. |
| **Output** | A list of n lists. The entry at each position holds that node's ancestors in ascending order. |
| **Key constraints** | n is between 1 and 1000. The graph holds no cycles and no repeated edges. |
| **Edge cases** | A node with no incoming path has an empty list. A node is not its own ancestor. |
| **Load-bearing** | An ancestor is any node with a directed path of one or more edges to the node, and each node's list is sorted ascending and free of repeats. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `0fe6860febe90de86d8d3c4c` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2173 — Minimum Weighted Subgraph With the Required Paths

| Field | Value |
| --- | --- |
| **QID** | 2173 |
| **Title** | Minimum Weighted Subgraph With the Required Paths |
| **Difficulty** | hard (1600) |
| **Topic** | Graph |
| **Operation** | In a directed weighted graph, find the smallest total edge weight of a subgraph in which two given sources can each reach a given destination, counting each chosen edge once, and report that total. |
| **Input** | Five parameters: n, the number of nodes labelled 0 to n-1; edges, a list of entries each giving a source, a target and a weight; and src1, src2 and dest, three node labels. |
| **Output** | A single integer: the smallest achievable total weight, or -1 when no such subgraph exists. |
| **Key constraints** | n is between 3 and 10^5. Weights are non-negative integers. |
| **Edge cases** | Edges shared by the two routes are counted once. When either source cannot reach the destination the answer is -1. |
| **Load-bearing** | Both sources must reach the destination within the SAME chosen set of edges, and the total counts each edge once however many routes use it. |
| **Author confidence** | medium |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `d6b2e52207487a1ad2868555` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2217 — Maximum Cost of Trip With K Highways

| Field | Value |
| --- | --- |
| **QID** | 2217 |
| **Title** | Maximum Cost of Trip With K Highways |
| **Difficulty** | hard (1600) |
| **Topic** | Dynamic Programming |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **NOT_DRAFTED** | The behaviour of 'Maximum Cost of Trip With K Highways' is not known to the author with enough confidence to write a specification safely. Sourcing the statement is an operator action. |
| **Author confidence** | none |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `12c2a0176e09e0cd2cce2b8a` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2224 — Design Video Sharing Platform

| Field | Value |
| --- | --- |
| **QID** | 2224 |
| **Title** | Design Video Sharing Platform |
| **Difficulty** | hard (1600) |
| **Topic** | Hash Table |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **STRUCTURALLY_BLOCKED** | 'Design Video Sharing Platform' is a DESIGN problem requiring a stateful class with several public methods called in sequence. The execution contract admits exactly one public method and one call per test case, so it cannot be made runnable under the current harness whatever its specification says. |
| **Author confidence** | n/a |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `a13f0712a7100e6b323e94a8` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2415 — Number of Nodes With Value One

| Field | Value |
| --- | --- |
| **QID** | 2415 |
| **Title** | Number of Nodes With Value One |
| **Difficulty** | medium (1300) |
| **Topic** | Tree |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **NOT_DRAFTED** | The behaviour of 'Number of Nodes With Value One' is not known to the author with enough confidence to write a specification safely. Sourcing the statement is an operator action. |
| **Author confidence** | none |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `1688b0e2d14aee43fe94626b` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q2449 — Maximum XOR of Two Non-Overlapping Subtrees

| Field | Value |
| --- | --- |
| **QID** | 2449 |
| **Title** | Maximum XOR of Two Non-Overlapping Subtrees |
| **Difficulty** | hard (1600) |
| **Topic** | Tree |
| **Operation** | *not drafted* |
| **Input** | *not drafted* |
| **Output** | *not drafted* |
| **Key constraints** | *not drafted* |
| **Edge cases** | *not drafted* |
| **NOT_DRAFTED** | The behaviour of 'Maximum XOR of Two Non-Overlapping Subtrees' is not known to the author with enough confidence to write a specification safely. Sourcing the statement is an operator action. |
| **Author confidence** | none |
| **Review risk** | HIGH |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `fc3e8563cb78d56293620213` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q3323 — Bitwise AND of Numbers Range

| Field | Value |
| --- | --- |
| **QID** | 3323 |
| **Title** | Bitwise AND of Numbers Range |
| **Difficulty** | medium (1300) |
| **Topic** | Bit Manipulation |
| **Operation** | Compute the bitwise AND of every integer in an inclusive range, and report the result. |
| **Input** | Two parameters, left and right, giving the inclusive bounds of the range with left no greater than right. |
| **Output** | A single integer: the bitwise AND of all integers from left to right inclusive. |
| **Key constraints** | Both bounds are between 0 and 2^31 - 1. |
| **Edge cases** | When the bounds are equal the answer is that value. A range spanning a power of two yields zero in the bits below it. |
| **Load-bearing** | Every integer in the range participates, and the operation applied across them is bitwise AND. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `f9ba201c0a628b4c3bd5b5ae` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |

### q3343 — Hamming Distance

| Field | Value |
| --- | --- |
| **QID** | 3343 |
| **Title** | Hamming Distance |
| **Difficulty** | easy (1000) |
| **Topic** | Bit Manipulation |
| **Operation** | Count the bit positions at which two integers differ, and report that count. |
| **Input** | Two parameters, x and y, both non-negative integers. |
| **Output** | A single integer: the number of differing bit positions. |
| **Key constraints** | Both values are between 0 and 2^31 - 1. |
| **Edge cases** | Two equal values differ in no positions. Leading zeros are shared and contribute nothing. |
| **Load-bearing** | The count is of POSITIONS where the two binary representations hold different bits. |
| **Author confidence** | high |
| **Review risk** | LOW |
| **Source basis** | title + boilerplate method name; no stored problem description |
| **Digest** | `c29a735be7152f6341805b14` |
| **Decision** | ☐ APPROVE ☐ REVISE ☐ REJECT |


---

## What "decision" means for a record that is not a draft

Eight of the 24 carry no specification. The three decisions still apply, but
they apply to the **finding**, not to a specification:

| | |
| --- | --- |
| **APPROVE** | The finding stands. For NOT_DRAFTED: the assistant should not draft this one, and it goes to a human author or is dropped. For STRUCTURALLY_BLOCKED: the question cannot run under the current contract and is excluded from reseeding until the contract changes. |
| **REVISE** | The finding is wrong or too cautious — the operator supplies the missing behaviour, or disputes the structural claim, and it is redrafted. |
| **REJECT** | The question is removed from the batch entirely. |

---

## Why five were not drafted

1961, 2066, 2217, 2415 and 2449 have titles the assistant could not map to a
canonical task with enough confidence to write something downstream work would
bind to.

This is the whole point of the exercise. A specification is not a guess that
gets corrected later — a hidden suite, a reference implementation and an Oracle
run are all generated *from* it, and every one of them will faithfully
implement whatever the specification says. A plausible-looking wrong
specification produces a question that passes every gate in this pipeline and
is still wrong. That is how the existing ~1,100 unusable questions came to be.

So the honest output for these five is a blank, not a draft.

---

## Three structural findings

These are not review items. They are properties of the harness that a
specification cannot fix.

**q2023 "Stock Price Fluctuation" and q2224 "Design Video Sharing Platform"
are DESIGN problems.** Both need a stateful class exposing several public
methods called in sequence. The execution contract admits exactly one public
method and one call per test case. No specification makes these runnable.

**q2035 takes a linked-list head.** The JSON argument envelope carries JSON
values; it cannot carry a constructed node chain. The same limit applies to
any question whose input is a pointer into a built structure.

**Extrapolation, stated as an estimate and not a measurement.** 3 of 24 —
12.5% — are structurally unrunnable. Against the 1,136-candidate reseed
population that projects to roughly 90-145 questions that cannot be reseeded
under the current contract at all. This sample was stratified for topic and
difficulty spread, not drawn to estimate this rate, so treat the number as an
order of magnitude that justifies measuring it properly, not as the answer.

---

## Risk, and what LOW does not mean

| Risk | Count | Meaning |
| --- | --- | --- |
| LOW | 7 | A wrong draft would be caught cheaply — the task is small, the output is scalar, and an error shows up immediately in a hidden case. |
| MEDIUM | 7 | A wrong draft survives casual reading and costs a regeneration cycle to unwind. |
| HIGH | 10 | A wrong draft is expensive or dangerous: subtle behaviour, or no draft exists at all. |

**`LOW` is a statement about blast radius, not correctness.** None of the 24
is verified. All 8 non-drafts are HIGH by construction, because an absent
specification blocks the question entirely.

Confidence, separately: high 9 / medium 7 / none 5 / n-a 3.

---

## What this phase did not do

Per §32K, and by design:

| | |
| --- | --- |
| Production writes | **0** |
| Oracle runs | **0** |
| New references | **0** |
| New hidden suites | **0** |
| Questions modified | 0 |
| Statements generated | 0 |
| Specifications frozen | 0 |
| Specifications marked operator-verified | 0 |
| Pre-images created | 0 |

No draft is bound to a question. Nothing in `groups_question` was read for
anything but title, topic and difficulty, and nothing was written.

---

## Security (§32I)

| Check | Result |
| --- | --- |
| Hidden test inputs or expected outputs in any draft | none - checked by regex over every load-bearing field |
| Reference implementations | none - `class Solution` and `def name(` both rejected by the emitter |
| External problem statements copied | none - every draft is original prose; no scraping was performed |
| Links to external problem sources | none - `leetcode.com` rejected by the emitter |
| Credentials | none |
| `.env` or secrets staged | none |

The credential check initially fired on q2081 and the finding was a false
positive: the pattern matched the English word in *"Find All People With
Secret"*, which is the question's own title. The pattern was narrowed to
credential shapes (`api_key=`, `secret_key`, `password=`, `sk-` prefixes)
rather than the bare word.

---

## The gate

**24 draft specifications and this review table now exist. That is the end of
the phase.** No statement generation, no suite generation, no reference
creation, no Oracle run follows automatically.

The next phase can only start on the operator's per-question decisions above.
A draft that reaches statement generation without being read is worth less
than no draft at all, because it carries the appearance of review without the
substance.
