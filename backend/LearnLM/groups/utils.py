"""
groups/utils.py

FIX-01 (CRITICAL): Output normalization for Judge0 test-case comparison.

Root cause: Judge0 returns stdout with a trailing newline ('42\n') while
expected_output stored in the DB does not ('42'). A raw '==' comparison
fails on every correct submission. This was the #1 reported bug
(correct code marked as Wrong Answer / 0 test cases passed).
"""


def normalize_output(raw: str) -> str:
    """
    Normalize program output for comparison.

    - Handles trailing/leading whitespace (Judge0 trailing '\\n').
    - Handles Windows line endings ('\\r\\n' -> '\\n').
    - Strips trailing whitespace per line without collapsing intentional
      internal blank lines.
    """
    if raw is None:
        return ''
    lines = [line.rstrip() for line in raw.strip().splitlines()]
    return '\n'.join(lines)


def all_test_cases_passed(judge0_results: list, hidden_test_cases: list) -> bool:
    """
    FIX-01 (extension): Guard against silent truncation.

    zip() stops at the shorter of the two lists. If Judge0 fails to return
    a result for one or more hidden test cases (crash, timeout, sandbox
    error), a naive `all(zip(...))` would only check the cases that DID
    come back and could report all_passed=True on an incomplete run.
    We explicitly fail the submission if the lengths don't match instead
    of silently comparing a subset.
    """
    if len(judge0_results) != len(hidden_test_cases):
        return False

    return all(
        normalize_output(r.get('stdout', '')) == normalize_output(tc['expected'])
        for r, tc in zip(judge0_results, hidden_test_cases)
    )
