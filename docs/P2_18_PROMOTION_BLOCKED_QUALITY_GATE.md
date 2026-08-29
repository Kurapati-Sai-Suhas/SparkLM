# P2.18 — Promotion blocked: the quality gate has never run on the pilot

**Status:** BLOCKED at §28A. No approval, no promotion, no publication. All
four pilot questions remain `DRAFT` / `UNVERIFIED`, and this phase wrote
nothing to production.

**Contains no grading truth** — digests and counts only.

---

## The premise that turned out to be false

The P2.18 brief listed, as already-established current state:

> All four: DRAFT · UNVERIFIED · approved reference · canonical reference ·
> **quality gate PASS** · Oracle PASS

Three of those four are true and were re-verified this phase. **The quality
gate has never been run on any of the four pilot questions.**

P2.17's Oracle output said so at the time, in every run:

> `'Ready for quality gate' is NOT readiness for ORACLE_VERIFIED.`
> `Still required: the P2.7h-1 hidden-test quality gate, and a human`
> `question-approval step that does not yet exist in this repository.`

"Ready for quality gate" was read as "gate passed". It is the opposite: it is
the gate's *precondition*.

---

## Preflight (§28A) — 15 of 17 pass on all four

| Check | q1940 | q1974 | q2057 | q2290 |
| --- | --- | --- | --- | --- |
| status `DRAFT` | PASS | PASS | PASS | PASS |
| trust `UNVERIFIED` | PASS | PASS | PASS | PASS |
| Approved reference exists | PASS #4 | PASS #5 | PASS #6 | PASS #7 |
| Reference canonical / active | PASS | PASS | PASS | PASS |
| Specification digest matches spec file | PASS | PASS | PASS | PASS |
| Oracle covers every hidden case | 15/15 | 14/14 | 16/16 | 14/14 |
| No Oracle conflicts | PASS | PASS | PASS | PASS |
| No unsettled / nondeterministic | PASS | PASS | PASS | PASS |
| Every Oracle row cites the active reference | PASS | PASS | PASS | PASS |
| Pre-image exists | PASS | PASS | PASS | PASS |
| Correct frozen batch `p27-pilot-2` | PASS | PASS | PASS | PASS |
| **Quality-gate spec exists (mutants)** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |
| **Quality-gate report exists** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |
| No learner submissions | 0 | 0 | 0 | 0 |
| Adaptive eligibility currently false | PASS | PASS | PASS | PASS |
| No `QuestionApproval` yet | PASS | PASS | PASS | PASS |

Artifact digests recorded for the eventual approval binding:

| Question | Artifact digest | Hidden suite digest |
| --- | --- | --- |
| q1940 | `1b9a4b5174fa250b968d1af14c5cd250…` | `995d97a836749fb6285abdf9288e6e8a…` |
| q1974 | `77625cb263c953e6d43357046ca1ae1e…` | `ac416cbf8e8c4071116f0285fcd7d53b…` |
| q2057 | `121fd1a8f497b99afd0fdc5fa8d83557…` | `0f1f617ea44705c286b340eb5c0a1987…` |
| q2290 | `b2934f3ba84d9d39df628cf28fc4bbd2…` | `7937bae0b5aa2a3c570d11be33a661b0…` |

---

## The blocker is structural, not a checklist preference

The lifecycle is a chain, and the missing artifact is its first link:

```
quality_gate  --spec <mutants>  --report-out <report>
      ↓ report
question_review  --quality-report <report>      → prints the digest
      ↓ digest
question_approve --quality-report <report> --digest <sha256>
      ↓ QuestionApproval row
question_promote                                 → trust_state
      ↓
question_status --to PUBLISHED                   → status
```

`--quality-report` is a **required** argument of both `question_review` and
`question_approve`. Verified by running it:

```
manage.py question_review: error: the following arguments are required:
    --quality-report
```

So without a gate report nothing downstream can even be invoked. There is no
flag that skips it, which is the design working as intended.

### What exists, and what does not

`backend/LearnLM/quality/` holds exactly two specs — `q1436` and `q3309`, the
two questions already published. `backend/LearnLM/reports/` holds their two
reports. **Nothing for 1940, 1974, 2057 or 2290**, and no document records
their mutants either.

P2.7 Phase 19 authored these suites mutant-first and drove the gate to a pass
during that session; the suite *plans* were persisted but the mutant specs
were not. They are not recoverable from the repository.

---

## What re-authoring them costs, and why it is weaker evidence

The gate needs, per question: an `input_contract`, and a list of mutants each
with `identifier`, `tier`, `description` and `source`. q3309's spec carries
10 mutants across both tiers. Thresholds are `TIER1_REQUIRED_KILL_RATE = 1.0`
and `TIER2_REQUIRED_KILL_RATE = 0.80`, and `EQUIVALENT` is reachable only
through a written `equivalence_argument`.

**Writing mutants now is not equivalent to writing them in Phase 19.** Then,
they were written *before* the suite and the suite was built to kill them —
which is why the gate refused four times before passing. Now the suites are
fixed and any mutant I write is written by someone who already knows what the
suite catches. That is the same-author problem that governed the reference
review, in a new place:

> a suite that kills mutants written against it is evidence about the author's
> consistency, not about the suite's coverage.

The gate would still be a real test — a badly-covered suite fails it
regardless of who wrote the mutants — but the result is weaker than the
Phase 19 result was, and it should not be recorded as though it were the
same thing.

**Recommendation.** If I author the specs, the mutant list should go to
operator review the same way the references did: you check that the
misconceptions are the ones a learner would actually have, not the ones the
suite happens to catch.

---

## Production integrity (§28G)

Snapshot taken at the end of P2.17 and again at the end of this phase:
**byte-identical**. This phase ran read-only commands only.

Unchanged: question count (2,926), reference count (7), Oracle executions
(188), approvals (2), submissions (44), pre-images (11), the grading-truth
fingerprint `43059aaa…005b750d`, and every pilot question's content,
boilerplate, wrapper, hidden suite, contract, status and trust digests.

---

## State

| | |
| --- | --- |
| q1940 | `DRAFT` / `UNVERIFIED` |
| q1974 | `DRAFT` / `UNVERIFIED` |
| q2057 | `DRAFT` / `UNVERIFIED` |
| q2290 | `DRAFT` / `UNVERIFIED` |
| Approvals | 2 → 2 (unchanged) |
| Publications | 2 → 2 (unchanged) |
| Submissions | 44 → 44 |
| Oracle evidence | 188 → 188 |
| Hidden suites | unchanged, all four digests identical |

The bank still has two trusted questions out of 2,926.
