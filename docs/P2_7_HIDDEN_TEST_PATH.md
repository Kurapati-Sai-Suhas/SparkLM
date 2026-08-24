# P2.7 — Hidden-test remediation path: built, dry-run, not applied

**q17 and q266 are ready.** The path is built, tested, mutation-verified, and
dry-run against production. Nothing was written.

The apply is blocked on one grant — deliberately, because the point of this
design is that the statement role *cannot* rewrite a key.

---

## 1. The narrow operation

`groups/management/commands/remediate_hidden_tests.py` — a separate command
from `remediate_statement`, writing exactly one column: `hidden_test_cases`.

Four independent limits, the same shape as statement repair:

1. `update_fields=["hidden_test_cases"]`;
2. every other captured field compared before and after **inside** the
   transaction — a difference reverts;
3. `require_pre_image` — frozen batch, matching pre-image, verifying digest;
4. a column-scoped role (below), so the database refuses anything wider.

Plus one this action class needs and statement repair does not:

### The stdin invariant

**The command refuses to change any `stdin`, add a case, or remove one.**

A "hidden test repair" that altered an input would change the question being
asked rather than the answer being recorded — under a name that sounds
mechanical. The approved repairs here touch stored *form* only, so the
invariant costs nothing and forecloses the dangerous version.

Comparison uses `provenance.case_identity`, the repository's one definition of
"the same input", not a second one invented here. Adding or removing cases is a
different action class and the command says so rather than quietly allowing it.

## 2. Privilege contract — a THIRD role

```sql
CREATE ROLE learnlm_hidden_test_rw LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE neondb TO learnlm_hidden_test_rw;
GRANT USAGE ON SCHEMA public TO learnlm_hidden_test_rw;

GRANT SELECT ON groups_question TO learnlm_hidden_test_rw;
GRANT UPDATE (hidden_test_cases) ON groups_question TO learnlm_hidden_test_rw;

GRANT SELECT, INSERT ON groups_remediationaction TO learnlm_hidden_test_rw;
GRANT SELECT ON groups_remediationbatch, groups_questionpreimage
  TO learnlm_hidden_test_rw;
```

**`learnlm_remediate_rw` was not broadened**, as instructed. The two roles are
mirror images:

| | statement role | hidden-test role |
|---|---|---|
| `content` UPDATE | **yes** | **no** |
| `hidden_test_cases` UPDATE | **no** | **yes** |
| status / trust_state / contract / boilerplate / wrapper | no | no |
| INSERT / DELETE / TRUNCATE | no | no |

**This is what makes statement-before-key a privilege rather than a
convention.** The role that repaired q963's statement physically cannot touch a
key; this role physically cannot touch a statement. Neither order can be
violated by a hurried operator.

Verify after creating it:

```bash
psql "$OWNER_URL" -c "select has_column_privilege('learnlm_hidden_test_rw','groups_question','hidden_test_cases','UPDATE') as can_edit_tests, has_column_privilege('learnlm_hidden_test_rw','groups_question','content','UPDATE') as can_edit_statement, has_table_privilege('learnlm_hidden_test_rw','groups_question','DELETE') as can_delete;"
```

Expect `t`, **`f`**, `f`.

The gate also refuses an **over-granted** role: if this one were given
`content` UPDATE as well, the command stops rather than using it.

## 3. Proposed changes — dry-run against production

Both case files are **derived from the frozen pre-images**, so every case the
repair does not touch is byte-identical by construction rather than by
retyping. The new values come from `execution_adapter.canonical_output` — the
execution contract's own renderer, not a second formatting rule.

### q17 — `remediation/q17_approved_cases.json`

```
pre-image        4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
projected after  704a1652751f5043e2d75794544cd63ba740e25e07c0355a61faa89690893cb5

case 1: unchanged   '["ad","ae","af","bd","be","bf","cd","ce","cf"]'  (str)
case 2: unchanged   '[]'  (str)
case 3: CHANGED  stdin '1'   []                 (list) -> '[]'                (str)
case 4: CHANGED  stdin '9'   ['w','x','y','z']  (list) -> '["w","x","y","z"]' (str)
```

Two of four cases. The **semantic answers are untouched** — for `digits='9'`
the letters are still w, x, y, z; only the storage type changes, to the exact
form cases 1 and 2 already use.

### q266 — `remediation/q266_approved_cases.json`

```
pre-image        1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
projected after  4df2af2733546b7c208a0407f91254f36244a2361065f6827c14299825a8f704

case 1: 'False' -> 'false'      case 3: 'True' -> 'true'
case 2: 'True'  -> 'true'       case 4: 'True' -> 'true'
```

All four, casing only. Every answer was already semantically correct; they were
simply unpassable, because the wrapper emits `true`/`false` and
`normalize_output` does not fold case.

Every `stdin` is held fixed in both, and the dry-run prints that explicitly.

## 4. Tests — 27, all passing

| requirement | covered |
|---|---|
| only `hidden_test_cases` changes | yes, field-by-field |
| statement cannot change | yes |
| status / trust_state cannot change | yes, incl. `is_adaptive_eligible` stays False |
| pre-image intact | yes, still holds the original suite |
| frozen batch required | yes |
| out-of-batch question rejected | yes |
| the control is not moved by a sibling repair | yes |
| no-op rejected | yes |
| action records the resulting digest | yes, and links the pre-image as the before-state |
| rollback still possible | yes, restores the original suite |

Plus: input change refused (apply **and** dry-run), case added refused, case
removed refused, corrupt pre-image refused, malformed JSON refused, missing
`expected_output` refused, empty suite refused, and structural guards that the
three role lists are disjoint and the two probes are mirror images.

## 5. Mutation — 12 killed / 12

One planted equivalent (a label in the dry-run plan).

Three real survivors were found and closed. Two are worth recording:

- **the input-change check could be deleted and the apply still refused** — the
  post-write re-check caught it. Only the *dry-run* was exposed. That matters:
  the dry-run is what an operator reads before approving, and a dry-run that
  displays an input change as acceptable is how it gets approved. Now tested at
  both points.
- **the privilege probe could be pointed at the wrong column** and no
  behavioural test noticed, because the probe only executes against production
  — locally the owner holds both columns, so a wrong probe passes for the wrong
  reason. Closed structurally.

The third was the untouched-field backstop, unreachable behind `update_fields`
and therefore needing a test that forces it to fire.

## 6. Full regression

**2,153 passed, 0 failed, 0 errors.**

## 7. Production untouched

```
q17     unchanged        q1436   unchanged
q264    unchanged        q1689   unchanged
q266    unchanged        q3309   unchanged
batch   CAPTURED, frozen, 7 members
fingerprint 783354ae1d3a32056885706fbba3bc5ceaa9a01e5a84604cfbb93a6bfed086b5  (unchanged since q963)
```

Only one `RemediationAction` exists — q963's statement repair.

## 8. Next action

1. Create `learnlm_hidden_test_rw` with the grants in §2 and verify with the
   `psql` line there.
2. Add `HIDDENTEST_*` credentials to `.env` yourself — do not send them to me.
3. Tell me, and I will wire the alias, re-run both dry-runs for your approval,
   then apply q17, verify, and stop before q266.

I would apply them **one at a time** with verification between, rather than
both in one pass. q963 was a single-field text change; these two rewrite the
column the grader compares against, and the first one is where a surprise would
show up.

---

```
q17 HIDDEN_TEST_REPAIR  = READY, NOT APPLIED
q266 HIDDEN_TEST_REPAIR = READY, NOT APPLIED
q963 KEY_REPAIR         = NOT STARTED
ORACLE                  = NOT STARTED
APPROVAL                = NOT STARTED
PROMOTION               = NOT STARTED
RESEED                  = NO
```
