# P2.7 — Rollback fixed, and proven on real production data

**q266 was restored from its immutable pre-image, by the ordinary hidden-test
role, on production.** The restored digest is exactly the captured one, the
predicted fingerprint was hit to the character, and the repair it undid is still
in the audit trail.

Rollback is no longer a claim in ten verification reports. It has run.

---

## A. The redesign

`pre_image` gained three pieces and lost one bad assumption:

```python
differing_fields(record, question)   # what actually has to be written
rollback_plan(batch, questions)      # the plan, computed WITHOUT writing
required_column_writes(targets)      # (table, column, UPDATE) per differing field
```

and the restore narrowed:

```python
-  question.save(using=using, update_fields=list(CAPTURED_FIELDS))
+  if fields:
+      question.save(using=using, update_fields=list(fields))
```

A question already at its captured state is now written **not at all** rather
than rewritten with identical values.

The guarantee is unchanged and still absolute: after the narrowed write the
**whole** captured state is re-read and digested against the pre-image, inside
the transaction, so a narrowed write is still proved to have reproduced all
seven fields.

## B. The differing-field mechanism

```
q266    hidden_test_cases differs                       -> ['hidden_test_cases']
q963-shaped   content differs                           -> ['content']
q1436-shaped  content, hidden_test_cases,
              boilerplate_code, contract differ         -> all four, and NOT
                                                           status/trust_state
```

Each is a test (§E). Nothing is special-cased: the requirement is derived from
the data every time.

## C. The corrected authorization model

```python
targets  = pre_image.rollback_plan(batch, questions)      # before any transaction
required = pre_image.required_column_writes(targets)
required, forbidden = ops.rollback_privileges(required)
    -> required:  UPDATE (hidden_test_cases) on groups_question
    -> forbidden: INSERT/DELETE/TRUNCATE on groups_question, plus UPDATE on
                  every captured column NOT being restored
gate_writing_role(alias, allowed=ops.ALLOWED_ROLLBACK_ROLES)
gate_write_privilege(alias, required=…, forbidden=…)
```

```python
ALLOWED_ROLLBACK_ROLES = (ALLOWED_REMEDIATION_ROLES | ALLOWED_HIDDEN_TEST_ROLES
                          | ALLOWED_CONTRACT_ROLES | ALLOWED_BOILERPLATE_ROLES)
```

The roles that may undo are the roles that may do. **`learnlm_preimage_rw` is
deliberately absent** — it holds nothing on `groups_question` and could never
restore one, which is precisely the role the old gate demanded.

`INSERT on groups_questionpreimage` is gone from the requirement. Rollback never
inserts a pre-image, and demanding it would have let the restorer forge its own
undo.

The plan is computed **before** the transaction opens, so a missing privilege is
a refusal with nothing written, not a half-finished restore.

## D. Partial-batch state, and the audit

- **The batch stays CAPTURED** unless every member is back at its capture. It
  now reads `CAPTURED` with four questions still repaired, which is true.
  Previously one restored question relabelled the whole batch `ROLLED_BACK`,
  with no command able to set it back.
- **One ROLLBACK action per question**, each carrying its own pre-image and its
  own post-digest, and a detail naming the fields restored. The old shape wrote
  a single action holding the *first* pre-image's digest — a claim about every
  restored question that was true of one.

## E. Tests and mutation

31 new tests in `groups/test_rollback_privileges.py`, built like the earlier
role-contract suite: real roles created with exact grants, entered with
`SET LOCAL ROLE`, each denial inside its own SAVEPOINT so a permission error
does not poison the next assertion. The database decides, not a mock — a mocked
check could not have caught this defect, because the code was asking for the
wrong privilege and the mock would have agreed.

Covered: A–P from the brief, including the hidden-test role sufficing, a role
without the column being refused *by Postgres*, INSERT on the pre-image table
being unnecessary, an over-granted role, statement-only and four-field
requirements, one missing privilege refusing before any write, alias handling,
lock, divergence, whole-state digest, audit, blast radius, and partial-batch
state.

**Mutation: 17 killed / 19.**

Attacked and killed: restoring all fields again; skipping the diff; selecting
the *equal* fields instead; defeating or deleting the post-restore digest check;
removing the divergence check; opening the transaction on `default`; removing
the row lock; dropping the audit action; recording only the first digest for a
multi-question rollback; relabelling the whole batch; reverting to the capture
role list; demanding INSERT on the pre-image table; dropping the forbidden list;
ignoring `--questions`; emptying the forbidden columns; making the rollback list
the capture list.

Two survivors, both equivalent, one planted and one discovered:

- **E1 (planted)** — the wording of a dry-run heading.
- **M12 (discovered)** — deleting `verify(pre_image)` from the restore loop.
  Proof of equivalence: any corruption that would fail `verify` also fails the
  post-restore whole-state digest comparison, which runs inside the same
  transaction; the observable end state is identical either way. `verify` only
  changes *when* the failure is detected — before any write rather than after a
  write that is then reverted. Kept for that fail-fast property, and recorded
  here rather than papered over with an artificial test.

Two real survivors from the first sweep were closed rather than explained: the
row lock could be removed from the apply pass while a "is there a lock anywhere"
assertion still passed (now: exactly two locked fetches asserted), and
`--questions` could be ignored entirely (now: a command-level test that the plan
names only the selected question).

## F. Real-role dry-run

```
PRE-IMAGE ROLLBACK  (DRY RUN)
  database        neondb        role  learnlm_hidden_test_rw
  production      True          operator  Suhas
  batch           p27-pilot-1 (CAPTURED)
  to restore      1
    q266    4df2af2733546b7c -> 1ba2e68f49b16317
      differing fields   ['hidden_test_cases']

  required privileges (derived from the fields above):
    UPDATE (hidden_test_cases) on groups_question
```

Exactly what the brief specified, through the ordinary repair role.

## G. The real rollback

```
Restored 1 question(s): [266]
Question state only. References, oracle executions, approvals and submissions
were not touched; one audit record per question was appended.
```

## H. Post-rollback state

```
live digest      1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
pre-image        1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
pre-image still verifies: yes

suite            ['False', 'True', 'True', 'True']      byte-identical
content · status · trust_state · execution_contract_version ·
boilerplate_code · hidden_wrapper_code · hidden_test_cases   all identical
```

The audit trail:

```
HIDDEN_TEST_REPAIR  2026-08-17 11:24:41  Suhas
  boolean casing True/False -> true/false to match the wrapper (form only)
  post_digest 4df2af27…a8f704
ROLLBACK            2026-08-18 16:23:27  Suhas
  restored hidden_test_cases
  post_digest 1ba2e68f…de26411
```

Both stand. Nothing was deleted or edited; 11 actions total.

## I. Whole-bank fingerprint

```
predicted before the rollback  e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8
measured after                 e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8
```

Character for character. That prediction was published in the previous report
before any code was changed, which makes it a forecast rather than a
description.

```
q264, q1689, q266   at their pre-images
q963  8da0eb14…   q17  704a1652…   q3309  8a342568…   q1436  0b2a79f2…
batch               CAPTURED, frozen, 7 members
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 2
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

## J. Regression

```
2320 passed, 0 failed, 0 errors   (groups, common, learning — 3m00s)
  rollback privilege contract      31 new
  pre-image + operator suites      99, all still passing unchanged
```

No infrastructure failures.

## K. Is rollback proven on real production data?

**Yes.** A production question was restored from its immutable pre-image by a
least-privilege role, the restored state is byte-identical to the capture, the
whole-state digest matches, the bank moved exactly as predicted, and the history
of both the repair and its undo survives.

One honest limit: what is proven is the **single-question** path. A
multi-question restore is covered by tests but has not run on production, and it
would need a role holding every differing column across the selected set — which
no single role does. Restoring a multi-field question like q1436 would today
require four separate roles and therefore cannot be done in one transaction.
That is a real limit of the current design, not a defect introduced here: it is
the price of the column-scoped separation, and it is worth deciding deliberately
before it is needed in anger.

## L. Exact next phase

**q266 is deliberately left rolled back**, as instructed — its keys are again
`True`/`False`, which the wrapper cannot match, so it is once more a question no
correct submission can pass. Reapplying it is a one-command repeat of a
verified operation whenever you want it.

The pilot's remaining work, unchanged:

1. **Re-apply q266's HIDDEN_TEST_REPAIR** (optional, trivially verified).
2. **The oracle run** against the two v3 questions — reference solution,
   execution, comparison against stored keys — then approval, then promotion.
   The first step that can make a question count for a learner.
3. **q963's keys** (`KEY_REPAIR_AFTER_ORACLE`) and **q264's control defect**,
   both still recorded and unrepaired.

---

```
ROLLBACK                 = COMPLETE + VERIFIED ON REAL PRODUCTION
EXECUTION CONTRACT PILOT = COMPLETE
q266                     = ROLLED BACK (deliberately)
ORACLE                   = NOT STARTED
SEMANTIC KEY REMEDIATION = NOT STARTED
BATCH                    = FROZEN (CAPTURED — 4 questions still repaired)
RESEED                   = NO
```
