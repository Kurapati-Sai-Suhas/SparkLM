# P2.19 — Pilot trust transition complete

**Status:** complete. All four pilot questions are `PUBLISHED` /
`ORACLE_VERIFIED` / adaptive-eligible.

**The trusted bank goes from 2 to 6.** This is the first time since the
content-trust milestone began that a question authored by this pipeline can
teach the adaptive model.

Contains no grading truth — digests and counts only.

---

## The lifecycle is not the order the brief assumed

§29C expected promotion to yield `status = PUBLISHED`. It does not.
`question_promote` writes `trust_state` and nothing else, and the two
commands **interlock**:

- `question_promote` on a DRAFT refuses: *"A draft cannot hold a proven
  answer key; advance its status first."*
- `question_status --to PUBLISHED` on an UNVERIFIED question refuses:
  *"Publishing an unverified question would make its unproven answers teach
  the adaptive model; promote it first."*

That looks like a deadlock and is not one. `PENDING_REVIEW` is the state
where verification happens, and it is the only legal path:

```
DRAFT ──status──▶ PENDING_REVIEW ──promote──▶ PENDING_REVIEW ──status──▶ PUBLISHED
                   UNVERIFIED                  ORACLE_VERIFIED            + eligible
```

Neither command can write the other's column: the roles hold no privilege on
it, which each command reports when it runs. Approval, verification and
publication are three separate attributable acts, and the interlock makes it
impossible to publish something unverified or verify something still a draft.

---

## What ran, per question

Identical five-step chain, one question at a time, each digest read from the
previous command's output rather than supplied by hand:

| Step | Command | Writes |
| --- | --- | --- |
| 1 | `question_review` | nothing — produces the artifact digest |
| 2 | `question_approve` | `QuestionApproval` row only |
| 3 | `question_status → PENDING_REVIEW` | `status` only |
| 4 | `question_promote` | `trust_state` + approval's `promoted_at/by` |
| 5 | `question_status → PUBLISHED` | `status` only |

| Question | Cases oracle-backed | Approval | Artifact digest |
| --- | --- | --- | --- |
| q1974 | 14/14 | 3 | `d0a249f80c6342081597bd7b793a6ff8…` |
| q1940 | 15/15 | 4 | `ba81141f836f2e3f6a0ef280…` |
| q2057 | 16/16 | 5 | `acffe391cf6aff274c2fb240…` |
| q2290 | 14/14 | 6 | `71f5045a3439fe6dbb813096…` |

Every approval row binds the artifact digest, the reference identity and its
source hash, the frozen quality outcome, and separate `executed_by`,
`reviewed_by`, `approved_by` and `promoted_by` attributions with timestamps.

Each digest was re-derived from live state by the command and checked against
the one supplied; a mismatch aborts. q1974's publication attempt was
correctly refused once, before promotion, and succeeded after.

---

## §29D — learner safety

**Finding, and it is not created by this phase: publication does not control
learner exposure.**

`_servable_questions()` filters on deliverability — it excludes placeholder
rows and questions with no hidden tests — and does **not** filter on `status`
or `trust_state`:

| | Count |
| --- | --- |
| Questions servable by the production recommender | **1,788** |
| Of those, `PUBLISHED` | 2 → **6** |
| Of those, `ORACLE_VERIFIED` | 2 → **6** |

All four pilot questions were **already being served to learners** in
`DRAFT` / `UNVERIFIED` state before this phase. Publishing them did not widen
learner exposure by one question.

What the transition changed is `is_adaptive_eligible` — whether a submission
against them may *teach the learner model*. That is the property the whole
trust pipeline gates, and the design is coherent: serving is gated on
deliverability, teaching is gated on trust.

It does mean roughly 1,782 questions are being served against unverified
answer keys, with only the *evidence* quarantined. That is a pre-existing
property of the bank, not a regression, and it is the strongest argument for
continuing the reseed.

### What the learner-facing payload contains

Verified directly against the live response construction:

```
id · title · difficulty · description · explanation ·
boilerplate_code · requested_topic · served_topic ·
topic_substituted · sample_case · advanced_xai
```

`sample_case` is built by `_sample_case`, which returns **`stdin` only** —
never `expected_output`, which for a single-case question *is* the answer
key. No `hidden_test_cases`, no `hidden_wrapper_code`, no reference source
and no grading internals appear anywhere in the payload.

Adaptive routing can see q1974 and the other three: all six trusted questions
are in the servable set and now carry `is_adaptive_eligible = True`.

---

## §29F — production integrity

Expected changes, and the complete diff:

```
question approvals        2  ->  6      (+4, one per pilot question)
q1940  DRAFT/UNVERIFIED  ->  PUBLISHED/ORACLE_VERIFIED    approvals 0 -> 1
q1974  DRAFT/UNVERIFIED  ->  PUBLISHED/ORACLE_VERIFIED    approvals 0 -> 1
q2057  DRAFT/UNVERIFIED  ->  PUBLISHED/ORACLE_VERIFIED    approvals 0 -> 1
q2290  DRAFT/UNVERIFIED  ->  PUBLISHED/ORACLE_VERIFIED    approvals 0 -> 1
grading-truth fingerprint  43059aaa...  ->  5b4be5db...
```

**Nothing else moved.** Verified by digest, not assertion: question count
(2,926), reference count (7), Oracle executions (188), code submissions (44),
pre-images (11), and for every pilot question the `content` digest,
`boilerplate_code` digest, `hidden_wrapper_code` digest, hidden case count,
hidden suite digest and `execution_contract_version`.

### Why the fingerprint moved, and why that is correct

The grading-truth fingerprint is computed over
`id | md5(hidden_test_cases) | md5(content) | status | trust_state |
execution_contract_version`. **It includes `status` and `trust_state`** — the
two columns this phase exists to change — so it had to move.

To show the parts that represent grading truth did not, the same digest
recomputed over the components *excluding* status and trust:

```
grading-truth-only fingerprint (no status/trust)
  1b69c227f0009a95be87501e8f822be2ca9e48e3e624fe6e78c7ffd088fccdf5
```

That is the value to compare across future phases when the question is
"did any answer key change".

**New baseline fingerprint:**
`5b4be5db8aa1130ceb809f521a425aaf920a7e1ec69515f3bc17d47f99f1ccf6`

---

## Final state

| Question | Status | Trust | Adaptive | Approvals |
| --- | --- | --- | --- | --- |
| q1436 | PUBLISHED | ORACLE_VERIFIED | yes | 1 |
| q1940 | PUBLISHED | ORACLE_VERIFIED | yes | 1 |
| q1974 | PUBLISHED | ORACLE_VERIFIED | yes | 1 |
| q2057 | PUBLISHED | ORACLE_VERIFIED | yes | 1 |
| q2290 | PUBLISHED | ORACLE_VERIFIED | yes | 1 |
| q3309 | PUBLISHED | ORACLE_VERIFIED | yes | 1 |

**Adaptive-eligible trusted count: 6** (was 2). Of 2,926 questions.

---

## Two defects found, neither fixed here

1. **`question_approve` crashes on Windows.** `_render_plan` writes `→`
   (→) to a cp1252 console and raises `UnicodeEncodeError` before doing
   anything. Worked around with `PYTHONIOENCODING=utf-8`; the command itself
   is unchanged. Same defect class as the one fixed in
   `kt_research.error_analysis` in P2.13. **A trust-critical command should
   not be edited mid-transition, so this is left for its own change.**

2. **The DB-backed test suites could not be run.** They target a local Docker
   Postgres and Docker Desktop is not running on this machine; the no-database
   suite passes 123/123 and the full 3,400-test regression passed twice
   earlier the same day, before Docker stopped. **No code changed in this
   phase**, so there is nothing new for those tests to catch — but the
   verification is genuinely absent rather than green, and is recorded as
   such.
