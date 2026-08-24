# P2.7 — Pre-image capture + rollback foundation (blocker J8)

**Status:** built and proven locally. Migration **not** applied to production.
No production grading truth touched. RESEED = NO.

---

## A. Pre-image schema

Three additive models in `groups/models.py`, logic in `groups/pre_image.py`.

**`RemediationBatch`** — a named set of questions one remediation may touch.
`batch_key` (unique), `purpose`, `state` (OPEN → CAPTURED → APPLIED →
ROLLED_BACK), `created_by/at`, `frozen_by/at`.

Membership is frozen before any modification. Without that, "roll back the
batch" has no fixed referent — a batch that can still grow after work started
cannot say what it would restore.

**`QuestionPreImage`** — the complete prior state of one question.

**`RemediationAction`** — append-only record of one applied change or one
rollback, carrying `post_digest`.

### Why a full copy, not a diff

A diff only restores if its base is still available and unchanged, and both
assumptions fail exactly when rollback matters: after a second remediation,
after a partial failure, after a manual edit. `hidden_test_cases` is also
nested JSON where a positional diff is meaningless the moment a case is
inserted or reordered. The pre-image stores the whole prior value — a few
kilobytes per question, and an entire class of "we can *almost* restore it"
outcomes removed.

## B. Fields captured

`CAPTURED_FIELDS`, pinned by a test against the remediable set:

```
content · status · trust_state · execution_contract_version
boilerplate_code · hidden_wrapper_code · hidden_test_cases
```

Plus, alongside:

- `case_identities` — `[{case, input, expected}]` per stored case;
- `was_adaptive_eligible` — **derived**, not a column. `Question.adaptive_eligible`
  does not exist; it is a property over `status`+`trust_state` (the stored
  column of that name lives on `CodeSubmission`). Restoring the two fields
  restores the derived value, and this records the trust boundary's own verdict
  at capture time as a cross-check;
- `state_digest`, `schema_version`, `captured_by`, `captured_at`, `batch`.

A structurally broken suite is captured **verbatim**, including a list-valued
`expected_output`. Refusing to capture those would make exactly the 48
questions most in need of repair the ones that cannot be repaired safely.

## C. Identity and digest strategy

**Borrowed, never reinvented** — per §2.

| thing | identity | source |
|---|---|---|
| hidden-test case | `provenance.case_identity(stdin)` = sha256(`normalize_output(stdin)`) | existing, shared with `OracleExecution` + `question_artifact` |
| executed input | `provenance.input_identity` | existing |
| expected output | `provenance.output_identity` | existing |
| question state | `pre_image.state_digest` | new, built from `question_artifact`'s length-prefixed framing |
| batch | `batch_key` (unique) | new |
| action | `RemediationAction` pk + `post_digest` | new |

A case therefore has **one** identity across pre-image, remediation, oracle
provenance and rollback. A second scheme is how a restore silently reattaches
the right answer to the wrong input.

The state digest reuses `question_artifact._frame` / `digest_of` — the same
length-prefixed encoding, with `PRE_IMAGE_SCHEMA_VERSION` emitted **first** so
a pre-image taken under one field set can never collide with one taken under
another.

## D. Immutability

- `QuestionPreImage.save()` refuses **every** update — write-once.
- Re-capturing the same question returns the **existing** row; the first
  pre-image wins, because it is the state rollback must return to. A second
  remediation cannot overwrite what the first one found.
- `UniqueConstraint(batch, question)` makes duplicates unrepresentable.
- `RemediationAction.save()` is append-only (the `QuestionApproval` pattern).
- All FKs are `on_delete=PROTECT`, so a pre-image cannot be orphaned by a
  cascade.
- Every capture records actor, timestamp, batch, schema version and digest.

## E. Batch model and two-phase contract

```
PHASE A  capture(batch, question, actor)   -> immutable pre-image
         freeze(batch, actor)              -> verifies EVERY capture,
                                              then closes membership
         (an empty batch cannot be frozen — that would look like a
          completed capture phase while authorising nothing)

PHASE B  require_pre_image(batch, question) -> raises unless frozen AND
                                               captured AND verifying
         ... the authorised remediation ...
         record_action(...)                 -> append-only, stores post_digest
```

`require_pre_image` **raises** rather than returning a falsy value, so a caller
cannot proceed by forgetting to check a result.

## F. Atomic rollback

`rollback(batch, actor, questions=None, allow_divergence=False)`.

Two passes, and this ordering is the design:

1. **Verify everything first** — every pre-image digest is recomputed, every
   live state is compared to its action's `post_digest`. Nothing is written
   during this pass.
2. **Then apply**, inside one `@transaction.atomic`, with a post-restore digest
   check per question. A failure anywhere reverts the entire batch.

A partial restore would leave the bank in a state that never existed: some
questions at their prior version, some remediated, and no record of which is
which. `questions=` restores a subset; all-or-nothing applies to whatever
subset was selected.

## G. Provenance during rollback

**Strategy A — preserve as evidence, mark superseded. Chosen after inspecting
the existing design, not guessed.**

`OracleExecution` is documented append-only; `QuestionApproval` is append-only
with only the promotion stamp mutable. Both are records of things that
*happened*: a reference ran, a person approved. Rolling back the data does not
un-happen either.

So `ROLLBACK_SCOPE = ("groups_question",)`. Rollback restores the question and
writes one append-only `RemediationAction` recording what it did. It does
**not** touch `ReferenceSolution` (so a withdrawn reference cannot be
reactivated), `OracleExecution`, `QuestionApproval`, `CodeSubmission` or any
learner state. A structural AST test asserts the restore path names none of
those models.

Trust is not fabricated in either direction: restoring returns `status` and
`trust_state` to the captured values, so a rollback can only ever *reduce*
trust to what was there before — never mint `ORACLE_VERIFIED`.

## H. Write-ahead safety behaviour

| attempt | outcome |
|---|---|
| modify with no pre-image | `CaptureIncomplete`, zero writes |
| modify before the batch is frozen (partial capture) | `CaptureIncomplete`, zero writes |
| freeze an empty batch | `CaptureIncomplete` |
| freeze with a corrupt capture | `DigestMismatch` — freezing verifies every pre-image |
| add a member to a frozen batch | `PreImageError` |
| rollback with a corrupt pre-image | `DigestMismatch`, nothing restored |
| rollback where live state diverged | `DigestMismatch` naming the questions, nothing restored |
| `allow_divergence=True` over corruption | still `DigestMismatch` — it is not a force flag |

`allow_divergence` permits restoring over a question that changed *after* the
remediation, and the override is recorded in the rollback action's detail so it
is visible afterwards.

## I. Test matrix

**51 tests in `groups/test_pre_image.py`**, all passing, synthetic database.

- capture completeness; `CAPTURED_FIELDS` pinned against the remediable set;
  derived eligibility recorded; broken suites capturable
- shared case identity, **including an input with trailing whitespace** that
  distinguishes `case_identity` from `output_identity`
- immutability: no edit, no overwrite by a second capture, unique constraint,
  append-only actions
- digest: deterministic; moves for **each** captured field (parametrised);
  moves on a nested JSON edit; moves on a non-identity edit inside a case;
  moves with the schema version
- write-ahead: no pre-image → rejected; unfrozen → rejected; rejected attempt
  changes nothing; empty batch unfreezable; frozen batch closed
- rollback: one question; multi-question; hidden-test and expected_output;
  contract change; statement change; repeated remediation → original state;
  subset restore; unknown question refused; unfrozen batch refused
- rollback safety: atomicity under induced failure; divergence detected;
  divergence override recorded; corrupt pre-image refused; unrelated questions
  untouched; audit evidence preserved; trust state not fabricated
- q264 control invariant (§K)

**Full regression: 2,040 passed, 0 failed, 0 errors.**

## J. Mutation results

**19 killed / 19. Zero unexplained survivors.** One planted equivalent
(message text only; the frozen-batch test asserts the exception type).

Every attack from the brief, all killed: skip pre-image, partial pre-image,
overwrite pre-image, wrong question identity (×2), wrong case identity, ignore
digest mismatch, invert the divergence guard, partial rollback, rollback
without a transaction, delete audit evidence, editable audit record, restore
only some fields (×2), drop the schema version, drop the suite from the digest.

**Four real survivors were found and closed.** The instructive one:

> **M8 — `case_identity` swapped for `output_identity`** survived the first
> sweep. Both are sha256, and for the test input `"110"` `normalize_output` is
> a no-op, so the two functions returned *the same digest*. The test asserted
> the right thing against an input that could not tell them apart. Closed with
> a case whose stdin carries trailing whitespace.

Also closed: rollback deleting prior audit rows (the count assertion still
held), and two digest-completeness gaps where the case-identity frames masked
a missing field frame.

## K. q264 control invariant — protected

Three tests:

- the control is **captured like any other question** (§9: capture it anyway);
- it survives a full batch **byte-identical** — captured, frozen, with a
  sibling remediated and the batch rolled back around it, its digest is
  asserted unchanged at both points;
- a change to it **is detectable** — the invariant is enforceable, not merely
  stated.

## L. Production data touched? **NO.**

```
database                     neondb
role                         learnlm_census_ro
questions                    2926
reference solutions          1
oracle executions            20
question approvals           0
questions PUBLISHED          0
questions ORACLE_VERIFIED    0
questions declaring v3       0
write privileges on groups_question: NONE (read-only role)

latest applied migration on PRODUCTION: 0043_glicko_snapshot
pre-image tables on PRODUCTION: 0   (migration 0044 NOT applied)
```

Migration `0044_pre_image_rollback` is **additive only** — three `CreateModel`,
two `AddIndex`, one `AddConstraint`. Zero `RunSQL`, `RunPython`, `AlterField`
or `RemoveField`, so it cannot alter existing grading data. It exists locally
and has **not** been applied to production.

Code changed: `groups/models.py` (+3 models), `groups/pre_image.py` (new),
`groups/test_pre_image.py` (new), `groups/migrations/0044_pre_image_rollback.py`
(new).

## M. Exact next step

**Authorization is required before anything below runs.**

1. **Apply migration 0044 to production.** Additive, no data change, but it is
   a production DDL write and needs the same explicit authorization the
   0038→0043 migration had.
2. **Build the operator command.** There is deliberately **no** management
   command yet — capture, freeze and rollback are library functions only, so
   the tooling cannot be pointed at production by accident. The command is the
   next code artifact and should default to dry-run, like `oracle_execute`.
3. **Capture pre-images for the 7-question pilot** (1689, 963, 3309, 17, 266,
   1436, and control 264) — capture only, no modification.
4. **Verify and freeze** that batch, then stop again for review before Phase B.

Note that the pilot role `learnlm_pilot_rw` has write scope; the census role
used throughout this phase does not. Which role performs the capture is a
decision for step 1.

---

## Final status

```
PRE-IMAGE/ROLLBACK TOOLING = PROVEN LOCALLY
PRODUCTION REMEDIATION     = NOT STARTED
PRODUCTION MIGRATION 0044  = NOT APPLIED
RESEED                     = NO
```

Awaiting explicit authorization before the first real production pre-image
capture.
