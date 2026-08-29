# P2.17 — Pilot Oracle evidence complete

**Status:** complete. All four pilot questions now carry fresh Oracle
evidence. **None is promoted, none is published, and no question approval
exists.** All four remain `DRAFT` / `UNVERIFIED`.

**Contains no grading truth.** No `produced_output`, no `stdin`, no hidden
case and no expected output appears in this document or in anything committed
alongside it — §27G. Everything below is a digest or a count.

---

## What ran

One question at a time, each independently verified before the next began.

| Order | Question | Reference | Contract | Cases | Rows | Result |
| --- | --- | --- | --- | --- | --- | --- |
| P2.15 | q1974 | #5 | `v3` | 14 | 28 | 14 agree · 0 conflict · 0 absent · 0 unsettled |
| §27A | q1940 | #4 | `v1` | 15 | 30 | 15 agree · 0 conflict · 0 absent · 0 unsettled |
| §27B | q2057 | #6 | `v1` | 16 | 32 | 16 agree · 0 conflict · 0 absent · 0 unsettled |
| §27C | q2290 | #7 | `v1` | 14 | 28 | 14 agree · 0 conflict · 0 absent · 0 unsettled |

**59 cases, 118 evidence rows, zero conflicts, zero unsettled, zero
nondeterministic.**

Every question was dry-run first. The dry run reported `DRY RUN — no
provenance recorded` each time, so `record=False` was confirmed inert before
`--execute` was given.

---

## Why rows are twice the case count

Each case produces **two** `OracleExecution` rows, not one. That is the
pipeline's determinism check, and it is deliberate:

> `OracleService.run` verifies determinism internally by running twice and
> discarding the second result. This pipeline instead calls it twice with
> `verify_determinism=False` and compares the two itself.

Hoisting the check keeps **both attempts as evidence** rather than throwing
one away. Verified independently for all four questions: exactly 2 rows per
`case_digest`, and both rows of every pair carry the same `output_digest` —
so the reference is demonstrably deterministic on all 59 inputs rather than
assumed to be.

This behaviour is already pinned by
`test_oracle_execution_pipeline.test_every_case_is_executed_twice`. **No new
test was written for this phase**: the invariant has a test, and a second one
asserting the same thing would be duplication rather than coverage.

---

## Independent verification (§27D)

Each question was checked against the database directly rather than against
the command's own summary. All fifteen checks passed for all four:

| Check | q1940 | q1974 | q2057 | q2290 |
| --- | --- | --- | --- | --- |
| Oracle evidence exists | 30 rows | 28 rows | 32 rows | 28 rows |
| Every hidden case represented | 15/15 | 14/14 | 16/16 | 14/14 |
| Two executions per case | ✓ | ✓ | ✓ | ✓ |
| Both runs agree (deterministic) | 0 bad | 0 bad | 0 bad | 0 bad |
| All executions `SUCCESS` | ✓ | ✓ | ✓ | ✓ |
| No `NONDETERMINISTIC` rows | ✓ | ✓ | ✓ | ✓ |
| Every row cites the active reference's hash | ✓ | ✓ | ✓ | ✓ |
| Every row records the question's contract | `v1` | `v3` | `v1` | `v1` |
| No row claims authority (`is_authoritative`) | ✓ | ✓ | ✓ | ✓ |
| Provenance schema recorded | v1 | v1 | v1 | v1 |
| Reference carries a specification digest | ✓ | ✓ | ✓ | ✓ |
| Pre-image in frozen batch `p27-pilot-2` | ✓ | ✓ | ✓ | ✓ |
| Question still `DRAFT` / `UNVERIFIED` | ✓ | ✓ | ✓ | ✓ |
| No `QuestionApproval` created | ✓ | ✓ | ✓ | ✓ |

Digests, for the record:

| Question | Suite digest | Reference `source_hash` | Specification digest |
| --- | --- | --- | --- |
| q1940 | `995d97a836749fb6285abdf9…` | `6e6e7573b44e9978…` | `3e837b04ce81d24f…` |
| q1974 | `ac416cbf8e8c4071116f0285…` | `7c8748a078305212…` | `eb8458fd460a64b6…` |
| q2057 | `0f1f617ea44705c286b340eb…` | `ab53947a8ace447f…` | `d8febbe3bb5f02ae…` |
| q2290 | `7937bae0b5aa2a3c570d11be…` | `85cf0800aa7b2b6c…` | `37f4230790e3c760…` |

Every suite digest is **identical before and after** execution. The Oracle
did not mutate a hidden test.

---

## Production integrity (§27F)

Field-level snapshot taken before and after. The complete diff:

```
oracle executions        98  ->  188      (+90)
  q1940 oracle rows       0  ->   30
  q2057 oracle rows       0  ->   32
  q2290 oracle rows       0  ->   28
reference #4 (q1940)  active=False -> True
reference #6 (q2057)  active=False -> True
reference #7 (q2290)  active=False -> True
```

**Nothing else moved.** Unchanged, verified by digest rather than by
assertion:

- Question count (2,926), reference count (7), approval count (2),
  submission count (44), pre-image count (11)
- **Grading-truth fingerprint** `43059aaa…005b750d`
- Every pilot question's `content` digest, `boilerplate_code` digest,
  `hidden_wrapper_code` digest, hidden case count and hidden suite digest
- Every pilot question's `status`, `trust_state` and
  `execution_contract_version`
- q1974's 28 existing rows — the earlier evidence was neither duplicated nor
  disturbed
- Every reference's `source_hash` and `specification_digest`

The three activations are the "expected canonical activation metadata" §27F
permits: the Oracle runs the **canonical** reference, so each question needed
its own activated before it could execute. Nothing was marked canonical by
hand — `reference_review activate` walked the state.

---

## What this evidence is not (§27E)

**Oracle evidence is not approval, and it is not trust.**

`is_authoritative` is `False` on all 118 rows. The command cannot write
`expected_output` — no writer for it exists in this phase — and it cannot set
`status`, `trust_state` or `adaptive_eligible`.

Reaching `ORACLE_VERIFIED` still requires, in order:

1. The **P2.7h-1 hidden-test quality gate** on each question.
2. A **`QuestionApproval` row** written by `question_approve` by a named
   operator. Zero exist for these four. Reference approval is not question
   approval — one approves a solution, the other approves the item.
3. `question_promote`, the only writer of `trust_state` besides
   `question_demote`.
4. Publication via `question_status`, a further separate step.

Only after 3 **and** 4 does `is_adaptive_eligible` become true and the
question begin producing learner evidence.

**Four of 2,926 questions have Oracle evidence. Two are published. The bank
is not trusted, and this phase did not make it so.**
