# P2.20 — Compromised suites rotated

**Status:** complete. q3309 and q1436 carry entirely new hidden suites, fresh
Oracle evidence and fresh approvals, and are back at `PUBLISHED` /
`ORACLE_VERIFIED` / adaptive-eligible.

**Zero exposed inputs survive.** All 25 cases across the two questions were
replaced; the intersection between the exposed input set and the live input
set is empty, verified by digest.

Contains no grading truth — digests and counts only.

---

## What was rotated

| | q3309 | q1436 |
| --- | --- | --- |
| Cases replaced | 12 / 12 | 13 / 13 |
| Exposed inputs still present | **0** | **0** |
| Suite digest | `c3230e3a…` → `9586708d…` | `5ab11f7c…` → `06e3c58d…` |
| Per-case digests changed | 12 of 12 | 13 of 13 |
| Categories | preserved exactly | preserved exactly |
| Quality gate | PASS — T1 1.0, T2 1.0 | PASS — T1 1.0, T2 1.0 |
| Fresh Oracle | 12 agree, 0 conflict | 13 agree, 0 conflict |
| Oracle rows | 24 → 48 | 26 → 52 |
| Approval | #1 → **#7** | #2 → **#8** |
| Reference | #2, unchanged | #3, unchanged |
| Submissions | 0, unchanged | 0, unchanged |

Old Oracle evidence was **retained, not deleted** — it records what was run
against the old suite. New evidence is appended and the new approval binds to
the new artifact digest.

### How the replacements were built

Every expected output was **computed from the approved canonical reference**,
never written by hand, so the suite cannot disagree with the reference it is
about to be oracled against. Three properties were verified locally *before*
anything was written:

1. **No exposed input reused** — each candidate `stdin` checked by sha256
   against every stored one. This fired: two q3309 candidates
   (`typical`, `duplicate_values`) collided, because the natural choices are
   the textbook examples and those were exactly what the original suite used.
   Both were replaced.
2. **Category multiset preserved exactly** — same labels, same counts.
3. **Mutant kills preserved** — the reviewed P2.18b catalogues re-run against
   the new suites. One q1436 Tier-2 mutant (`t2-skip-first-edge`) survived
   the first draft at 0.83; rather than accept a passing-but-weaker suite, one
   `unordered_edges` case was redesigned so the first edge's source is a later
   edge's destination, which is exactly what that mutant mishandles. Final
   rates are 1.0 / 1.0 on both questions.

Designing a suite to kill known mutants is legitimate — it is what a suite is
for. It is the *reverse* of P2.18b's concern, which was writing mutants to
fit a known suite.

---

## The finding this rotation surfaced

**The v3 stored-stdin format cannot express an empty string argument.**

`execution_adapter` line 343:

```python
lines = [line for line in stdin.split("\n") if line.strip() != ""]
```

Blank lines are filtered, so `"anything\n\n"` binds as one argument for a
two-parameter signature and is refused as `CONTRACT_MISMATCH`. Quoting it as
`"anything\n\"\"\n"` binds — but to the literal two-character string `""`,
not to an empty string. Both are wrong, and the second is worse because it
*looks* correct.

This mattered because q3309's statement explicitly specifies the empty-needle
case ("If needle is an empty string, return 0"), and three of its reviewed
mutants exist precisely to probe that branch.

**How it showed up.** The first gate run failed with *"4 mutants could not be
executed"* — `t1-empty-needle-not-found`, `t1-last-occurrence`,
`t1-off-by-one-window`, `t2-empty-branch-removed`. Those are exactly the four
not killed by cases 1–4, so they were the only ones to reach case 5 and hit
the binding failure. The gate reported a symptom four steps from its cause.

**The fix.** Encode the whole argument list as a single-line JSON envelope:

```
["anything",""]      ->  arguments ['anything', '']
```

which is the v3 canonical form. The original suite used the same technique —
its case 4 also had one non-blank line and bound to two arguments with an
empty second. Only the input encoding changed; the expected output was
already `0` and was not touched.

---

## Order of operations, and one correction to the P2.7h-34 design

Executed order: demote → rotate inputs → rotate answers → gate → oracle →
approve → promote.

**The P2.7h-34 rotation design claimed step 1 (withdraw to
`PENDING_REVIEW`) would stop the question being served, closing the window in
which inputs and answers disagree. That is false**, as P2.19 established:
`_servable_questions()` filters on deliverability, not on `status` or
`trust_state`. Withdrawal would not have closed the window.

Demotion does close the window that matters — `is_adaptive_eligible` goes
False immediately, so nothing learned during the rotation can teach the
model. But a learner could still have been *served* one of these questions
between the input rewrite and the answer rewrite, and would have been graded
against a mismatched pair. The two commands were run back to back to keep
that window to seconds, and both questions have zero submissions across all
time, but **the window is real and the current tooling cannot eliminate it**.
A dedicated `rotate_suite` command writing both columns in one transaction
would.

Two role gates fired correctly along the way: `remediate_inputs` refused
`learnlm_remediate_rw` ("only `learnlm_hidden_test_rw` may perform it"), and
`question_status` refused to publish an UNVERIFIED question.

---

## Security (§30I)

- **No answer-key file was committed.** All four rotation plans live in
  `backend/LearnLM/remediation/` and match existing ignore patterns
  (`*_case*_input.json`, `*_approved_cases.json`); each was confirmed ignored
  by `git check-ignore` before any commit.
- **No hidden input or expected output was printed.** The remediation
  commands echo case values by design, so their output was filtered at the
  shell for every applied run.
- Production evidence is in the database only.
- Git history was **not** rewritten. The old exposed keys remain in history
  and are a separate remediation item — now less urgent, because those keys
  no longer open anything: every case they describe has been replaced.

---

## Final state

| | q3309 | q1436 |
| --- | --- | --- |
| Status | PUBLISHED | PUBLISHED |
| Trust | ORACLE_VERIFIED | ORACLE_VERIFIED |
| Adaptive eligible | yes | yes |
| Old suite invalidated | **yes** | **yes** |
| Submissions | 0 | 0 |

**Adaptive-eligible trusted count: 6**, unchanged — trust was withdrawn and
re-established on evidence that now covers a suite nobody has seen.
