# P2.7 — q963 statement repair: tool built, write blocked

**The repair did not run.** No role in this system can write
`groups_question.content`, and the one that can is the one you excluded.

Your seven decisions are recorded. The tool that performs the repair now exists,
is tested and mutation-verified, and is waiting on a role.

**Grading truth unchanged. Zero remediation actions.**

---

## Pre-repair safety — all four conditions hold

```
batch p27-pilot-1     state=CAPTURED   frozen_at=2026-08-17 05:32:58 UTC   frozen=True
q963 pre-image        06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
q963 live digest      06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c   match
q264 control          unchanged
fingerprint           1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f   IDENTICAL
remediation actions   0
```

Nothing aborted. The repair is safe to perform the moment it can be performed.

## The blocker

```
role                    table UPDATE   content UPDATE
learnlm_preimage_rw     False          False
learnlm_census_ro       False          False
learnlm_pilot_rw        False          False
neondb_owner            True           True
```

Only the owner can write the statement, and you ruled it out for remediation
writes when `learnlm_preimage_rw` was created. Existing roles: exactly the
three above.

**This is the design working, not a gap.** The capture role holds `SELECT` on
`groups_question` precisely so it can preserve a question and *cannot* change
it. Granting it `UPDATE` would collapse capture and remediation into one
privilege and make the pre-image guarantee circular — the role that writes the
safety net would also be the role that can defeat it.

### The grant needed

A fourth role, `learnlm_remediate_rw`, with **column-level** `UPDATE` on
exactly one column — the same shape as the freeze grant you already made:

```sql
CREATE ROLE learnlm_remediate_rw LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE neondb TO learnlm_remediate_rw;
GRANT USAGE ON SCHEMA public TO learnlm_remediate_rw;

GRANT SELECT ON groups_question TO learnlm_remediate_rw;
GRANT UPDATE (content) ON groups_question TO learnlm_remediate_rw;

-- to write its own audit trail
GRANT SELECT, INSERT ON groups_remediationaction TO learnlm_remediate_rw;
GRANT SELECT ON groups_remediationbatch, groups_questionpreimage
  TO learnlm_remediate_rw;
```

Verify with:

```bash
psql "$OWNER_URL" -c "select has_column_privilege('learnlm_remediate_rw','groups_question','content','UPDATE') as can_edit_statement, has_column_privilege('learnlm_remediate_rw','groups_question','hidden_test_cases','UPDATE') as can_edit_tests, has_table_privilege('learnlm_remediate_rw','groups_question','DELETE') as can_delete;"
```

Expect `t`, **`f`**, `f`. The middle one is the point: the database itself
refuses a hidden-test write, so statement-repair-before-key-repair is enforced
by privilege rather than by discipline.

Then add `REMEDIATE_*` credentials to `.env` yourself — do not send them to me —
and I will wire a `remediate` alias exactly as with `preimage`.

## What was built

**`groups/management/commands/remediate_statement.py`** — dry-run by default,
writes one column of one question, records an append-only action.

Four independent limits on what it can touch:

1. `update_fields=["content"]` on the save;
2. every other captured field compared before and after, inside the
   transaction — a difference aborts and reverts;
3. `pre_image.require_pre_image` refuses unless the batch is frozen, the
   question has a pre-image, and that pre-image still verifies;
4. the role is expected to hold column-level `UPDATE` on `content` alone.

A separate `ALLOWED_REMEDIATION_ROLES` list, disjoint from the capture list —
sharing one would grant the capture role the write it exists to make
unnecessary. The capture role and the owner are both refused for remediation.

The dry-run prints a unified diff of the statement change before anything
happens.

### Tests and mutation

**24 tests**, covering: no pre-image → refused; unfrozen batch → refused;
corrupt pre-image → refused; dry-run writes nothing; only `content` changes;
the control untouched; the pre-image still holds the original; **rollback
restores it**; the action records both digests; no-op refused; empty file
refused; and structural guards (every `save` names `update_fields`; the module
mentions no grading-data field; it uses the remediation role list).

**Mutation: 10/11 killed**, one planted equivalent.

Two real survivors were found and closed, both defence-in-depth properties my
first tests never exercised:

- **the untouched-field check** could be deleted unnoticed, because
  `update_fields` already makes it unreachable. A guard that cannot fire is an
  unverified claim, so there is now a test that forces it to fire and asserts
  the write reverts.
- **the transaction** could be removed unnoticed. Now a test makes the audit
  write fail and asserts the statement change does not survive it — otherwise
  grading truth could move with nothing recording who moved it.

**Full regression: 2,110 passed, 0 failed, 0 errors.**

## Decisions recorded

| q | decision | status |
|---|---|---|
| 264 | KEEP as control, defect noted for a later batch | recorded |
| 1689 | MANUAL_REVIEW, no external import | recorded, untouched |
| 963 | STATEMENT_REPAIR approved (§B wording) | **ready, blocked on role** |
| 1436 | statement stands; contract repair; case 2 input decision | recorded, not started |
| 3309 | preserve empty-needle; relax constraint; contract repair | recorded, not started |
| 17 | mechanical hidden-test repair | recorded, not started |
| 266 | boolean casing normalisation | recorded, not started |

The approved q963 statement is held ready and will be applied verbatim from a
file — it is not stored in the command, in an argument, or in shell history.

## Next action

1. Create `learnlm_remediate_rw` with the grants above.
2. Add `REMEDIATE_*` to `.env`.
3. Tell me, and I will wire the alias, dry-run the repair with a full diff for
   your approval, then apply it and stop.

---

```
q963 STATEMENT_REPAIR  = READY, BLOCKED ON WRITE ROLE
q1436 CONTRACT_REPAIR  = NOT STARTED
q3309 CONTRACT_REPAIR  = NOT STARTED
q17 HIDDEN_TEST_REPAIR = NOT STARTED
q266 HIDDEN_TEST_REPAIR= NOT STARTED
KEY_REPAIR             = NOT STARTED
ORACLE                 = NOT STARTED
APPROVAL               = NOT STARTED
PROMOTION              = NOT STARTED
RESEED                 = NO
```
