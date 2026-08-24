# P2.7 — Remediation gate fix: statement repair no longer requires INSERT

**My bug, found by your role being correct.** The gate demanded a privilege the
operation never needed. Fixed, tested against a real role, and mutation-verified.

Production unchanged. Zero writes.

---

## A. Exact source of the erroneous INSERT requirement

**A reused capture gate — I parameterised the table but not the privilege.**

When the remediation path was added, `gate_write_privilege` was generalised
from the capture path:

```python
def gate_write_privilege(alias, table=None, privilege="INSERT"):   # ← default
    target = table or "groups_questionpreimage"
    cursor.execute("select has_table_privilege(current_user, %s, %s)",
                   [target, privilege])
```

and `remediate_statement` called it with:

```python
write_probe_table="groups_question"      # table swapped, privilege inherited
```

So a statement repair asked "can this role INSERT into `groups_question`?" —
a privilege it neither needs nor should ever hold. Capture legitimately needs
INSERT on the pre-image table; remediation needs column-scoped UPDATE. One
default carried the wrong semantics across.

Two aggravating properties worth naming:

- **it was also table-scoped.** Even with the privilege corrected to UPDATE, a
  table-level check returns TRUE when a role holds UPDATE on *any* column, so
  it could not have distinguished `UPDATE (content)` from `UPDATE` on
  everything — it would have passed the narrow grant and a dangerous one
  identically.
- **it failed in the safe direction only by luck.** The role was right and the
  gate was wrong; had the grant been broader, the gate would have been silent.

## B. Corrected gate logic

The gate now proves **the operation it is about to perform**, named as a
`(table, column, privilege)` triple:

```python
CAPTURE_PROBE           = (("groups_questionpreimage", None,      "INSERT"),)
STATEMENT_REPAIR_PROBE  = (("groups_question",         "content", "UPDATE"),)
```

`has_column_privilege` when a column is named, `has_table_privilege` otherwise.

And a second half that did not exist before — the privileges the operation must
**not** hold:

```python
STATEMENT_REPAIR_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "status", "UPDATE"),
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
)
```

**An over-granted role is now refused, not trusted.** The written contract
became a run-time check: if someone later grants the remediation role
`UPDATE` on the whole table, the command stops rather than quietly using it.

Nothing else was relaxed. The production-identity check, the role allow-list,
the frozen-batch check, pre-image validation and operator authorization are
untouched — mutation confirms each still fires.

The `forbidden` half applies on **production only**, for the same reason the
role allow-list does: a local test database is owned by a role holding
everything, and demanding least privilege there would make the gates
untestable. The `required` half runs everywhere. The forbidden check is
exercised directly against purpose-built roles instead.

## C. Role contract — unchanged and now enforced

```
groups_question              SELECT = true
groups_question.content      UPDATE = true
groups_question              INSERT = false · DELETE = false · TRUNCATE = false
all other columns            UPDATE = false
groups_remediationaction     SELECT, INSERT
groups_remediationbatch      SELECT
groups_questionpreimage      SELECT
```

`learnlm_remediate_rw` was already exactly this. **No grant was changed and
none is needed.**

## D. Tests

New `groups/test_remediation_role_contract.py` — 15 tests that build roles with
exact grants and become them with `SET LOCAL ROLE`, so **the database decides
what is permitted, not a mock**. A mocked check could not have caught this bug:
mocks assert what the code asks for, and the code was asking for the wrong
thing.

| requirement | test |
|---|---|
| **A** dry-run passes | `test_the_gate_passes_for_the_intended_role` |
| **B** the statement UPDATE really works | `test_the_role_can_update_the_statement` |
| **C** hidden_test_cases refused | `test_the_role_cannot_update_hidden_tests` |
| **D** status/trust_state refused | two tests, both denials from Postgres |
| **E** INSERT not required | `test_the_gate_does_not_require_insert_on_question` + `test_the_role_cannot_insert_a_question` |

Plus: DELETE refused; the audit row can be appended; a role missing
`UPDATE (content)` is refused; an over-granted role is refused; the probe is
column-scoped; the forbidden set covers every grading-truth column; capture
still probes its own operation.

One mechanical detail worth recording: each denial runs inside its own
SAVEPOINT. A permission error aborts the surrounding Postgres transaction, so
without one the first denial would make every later statement fail for the
wrong reason — the tests would still pass, while measuring an aborted
transaction rather than a privilege.

## E. Mutation results

**10 killed / 10.** One planted equivalent (a falsy-default expression
rewritten).

| attack | outcome |
|---|---|
| require INSERT again — *the original bug* | killed |
| remove the content UPDATE check | killed |
| column-level → table-level | killed |
| permit `hidden_test_cases` UPDATE | killed |
| permit `status` UPDATE | killed |
| permit `trust_state` UPDATE | killed |
| column checks fall back to table-level | killed |
| bypass production identity | killed |
| bypass frozen batch / pre-image validation | killed |
| the over-granted check stops checking | killed |

One mutant had a bad anchor and was re-aimed and re-run rather than dropped.

## F. Full regression

**2,126 passed, 0 failed, 0 errors.**

An intermediate run had one failure: the repo's own guard caught the five
`REMEDIATE_*` variables undocumented. Registered in `FEATURE_FLAGS.md` — the
guard working, not a defect.

## G. Production unchanged

```
batch p27-pilot-1     state=CAPTURED  frozen=True
members               7   [17, 264, 266, 963, 1436, 1689, 3309]
remediation actions   0
q963 pre-image        matches live
q264 control          unchanged
fingerprint           1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f  IDENTICAL
```

**Zero production writes.** The failed attempt stopped at the gate, before the
transaction opened.

## H. Retry command for the q963 dry-run

```bash
python manage.py remediate_statement --alias remediate --batch p27-pilot-1 --question 963 --content-file <approved-statement-file> --reason "adjudication record section B: any-orientation reading; Example 1 explanation described Example 2 input; Example 2 key 0 was false for its own input" --operator Suhas
```

Unchanged from before — the fix was entirely inside the gate. Adding
`--apply --confirm` performs the repair.

---

```
q963 STATEMENT_REPAIR = GATE FIXED, DRY-RUN READY
PRODUCTION WRITE      = 0
REMEDIATION           = NOT STARTED
RESEED                = NO
```

Stopping here as instructed. Next: re-run the dry-run, you review it, apply,
verify, stop.
