# P2.7 — q963 statement repair: dry-run complete, awaiting apply

**Nothing was written.** The remediation role verifies, the batch verifies, the
diff is below, and production is byte-identical to before the dry-run.

---

## A. Remediation role identity

```
endpoint          ep-blue-hat-aj7p2x8v-pooler
current_database  neondb
current_user      learnlm_remediate_rw
session_user      learnlm_remediate_rw
server_version    17.10 (29ad1b7)

LOGIN True · SUPERUSER False · CREATEDB False · CREATEROLE False
REPLICATION False · BYPASSRLS False
inherits from: NONE
```

## B. Exact write scope — **precisely one column**

Table level on `groups_question`: `SELECT=T`, and `INSERT`, `UPDATE`, `DELETE`,
`TRUNCATE` all **false**. Table-level `UPDATE` reading false is correct for a
column grant.

I audited **every** column, not the listed ones — a grant on a column nobody
thought to check is exactly the grant that would matter:

| column | UPDATE |
|---|---|
| **content** | **TRUE** |
| id, title, base_difficulty, topic_id | false |
| hidden_test_cases | **false** |
| boilerplate_code, hidden_wrapper_code | false |
| execution_contract_version | false |
| status, trust_state | false |

One of eleven columns is writable. `adaptive_eligible` does not exist on
`groups_question` — it is a derived property over `status`+`trust_state`, and
both of those are locked, so it cannot move either.

Elsewhere:

```
groups_remediationaction     ['SELECT', 'INSERT']   ← by design, for the audit trail
groups_remediationbatch      ['SELECT']
groups_questionpreimage      ['SELECT']
groups_questionapproval      []
groups_referencesolution     []
groups_oracleexecution       []
groups_codesubmission        []
```

**The database itself now enforces statement-repair-before-key-repair.** This
role cannot write a hidden test even if the code asked it to — and it cannot
alter or delete a pre-image, so the route back survives anything it does.

## C. Frozen-batch verification

```
batch p27-pilot-1     state=CAPTURED   frozen=True
members               7   [17, 264, 266, 963, 1436, 1689, 3309]
remediation actions   0
q264 control          unchanged
```

## D. q963 pre-image verification

```
pre-image digest   06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
live digest        06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
verify()           passes
```

## E. Unified diff — the exact change

`860 → 838 bytes`

```diff
--- current
+++ proposed
@@ -1,2 +1,2 @@
-Given a set of points in the plane, write a function to find the minimum area rectangle that can be formed by these points. The vertices of the rectangle must be among the given points and each side must be parallel to either the x-axis or y-axis. If no such rectangle exists, return 0.
+Given a set of points in the plane, find the minimum area of any rectangle formed from these points, with sides not necessarily parallel to the coordinate axes. The four vertices must be among the given points. If no such rectangle exists, return 0.

@@ -5,3 +5,3 @@
 Output: 2
-Explanation: The minimum area rectangle is formed by points (0,0), (0,1), (1,0) and (1,1).
+Explanation: The four points form a square rotated 45 degrees, with side length sqrt(2) and area 2.

@@ -9,4 +9,4 @@
 Input: [[0,0],[0,1],[1,0],[1,1],[2,1],[2,0]]
-Output: 0
-Explanation: No rectangle can be formed with the given points.
+Output: 1
+Explanation: The unit square (0,0), (0,1), (1,0), (1,1) has area 1.

@@ -24,3 +24,3 @@
-**Output 2:** `0`
+**Output 2:** `1`

@@ -28,2 +28,2 @@
-**Output 3:** `2`
+**Output 3:** `1`
```

Five edits, all approved in adjudication §B:

1. the constraint sentence → any-orientation;
2. Example 1's explanation → describes its own input (it previously described
   Example 2's);
3. Example 2's output `0` → `1` and its explanation → the unit square that
   input actually contains;
4. the `### Examples` block's Output 2 `0` → `1`, kept consistent with (3);
5. Output 3 `2` → `1`, the independently computed value.

**One thing to be explicit about:** edits 3–5 change the statement's *displayed*
examples. They do **not** change `hidden_test_cases` — the stored keys are
still `['2', '0', '2', 1]`, and cases 2 and 3 remain wrong until
`KEY_REPAIR_AFTER_ORACLE`. After this repair the statement and the stored keys
will *disagree*, deliberately and visibly, which is the intended intermediate
state: the statement becomes correct first, and the keys are re-derived against
it afterwards.

## F. Fields that would change

| would change | must remain unchanged |
|---|---|
| `content` (860 → 838 bytes) | `status`, `trust_state`, `execution_contract_version`, `boilerplate_code`, `hidden_wrapper_code`, **`hidden_test_cases`** |

```
current digest    06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
projected after   8da0eb14d1a98d414d0aa3e9ba6518980db46b5065e3c8907e3e839fd27ab688
```

## G. Dry-run caused zero writes — confirmed

Re-verified after the dry-run:

```
remediation actions   0
q963 pre-image matches live   True
q264 unchanged                True
```

The live q963 digest is still exactly its pre-image digest.

## H. Post-dry-run fingerprint

```
1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f   IDENTICAL
```

## I. The command for the first real write

```bash
python manage.py remediate_statement --alias remediate --batch p27-pilot-1 --question 963 --content-file <approved-statement-file> --reason "adjudication record section B: any-orientation reading; Example 1 explanation described Example 2 input; Example 2 key 0 was false for its own input" --operator Suhas --apply --confirm
```

Identical to the dry-run above plus `--apply --confirm`. The approved wording
stays in a file — never an argument, never shell history.

On apply it will: take a row lock, write `content` alone with
`update_fields`, re-read and compare every other captured field inside the
transaction, compute the after-digest, and append one `RemediationAction`
carrying the reason and both digests. Any surprise reverts the whole thing.

Also new this phase: `settings.py` gained a `remediate` alias, present only
when `REMEDIATE_USER` is set — a third alias rather than reusing `preimage`,
because the capture role must not be able to change what it preserves and the
remediation role must not be able to append pre-images.

---

```
q963 STATEMENT_REPAIR = DRY-RUN READY
PRODUCTION WRITE      = NOT YET APPLIED
REMEDIATION           = NOT STARTED
RESEED                = NO
```

Say the word and I will apply it, verify, and stop.
