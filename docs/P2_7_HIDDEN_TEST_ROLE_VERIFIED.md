# P2.7 — Hidden-test write role verified, q17 dry-run clean

`learnlm_hidden_test_rw` exists, is scoped to exactly one column, and the q17
dry-run reproduces the digest projected before the role existed. **Nothing was
written.** q266 was not touched.

---

## A. Wiring (the only repo changes in this phase)

| file | change |
|---|---|
| [settings.py](LearnLM/backend/LearnLM/LearnLM/settings.py) | conditional `hiddentest` alias, present only when `HIDDENTEST_USER` is set; no pool; `TEST: {MIRROR: default}` |
| [FEATURE_FLAGS.md](LearnLM/docs/FEATURE_FLAGS.md) | the five `HIDDENTEST_*` variables documented — the repo guard fails a variable read in code and absent here |

No command, gate, model or migration changed. The path was already built and
mutation-tested last phase; this phase only connected it and looked.

## B. Step 1 — role identity

```
endpoint          ep-blue-hat-aj7p2x8v-pooler
current_database  neondb
current_user      learnlm_hidden_test_rw
session_user      learnlm_hidden_test_rw
server_version    17.10 (29ad1b7)
```

`session_user` is checked as well as `current_user`, so the identity cannot be
a `SET ROLE` sitting on top of a wider login.

```
LOGIN        True    SUPERUSER  False    CREATEDB     False
CREATEROLE   False   REPLICATION False   BYPASSRLS    False
inherits from  NONE
```

No role membership at all — the privileges below are the whole of what it can
do, not a subset of something inherited.

## C. Step 2 — exact column scope

Every column of `groups_question`, asked of the database **as the role itself**
via `has_column_privilege`, not the seven the design happened to name:

```
table-level:  SELECT=T  INSERT=F  UPDATE=F  DELETE=F  TRUNCATE=F

id                           F      hidden_wrapper_code          F
title                        F      execution_contract_version   F
content                      F      status                       F
base_difficulty              F      trust_state                  F
topic_id                     F
hidden_test_cases            T   <- the only one
boilerplate_code             F
```

11 columns, 1 writable. `content=F` is the load-bearing entry: **this role
cannot edit a statement**, and `learnlm_remediate_rw` cannot edit a key.

```
groups_remediationaction    SELECT, INSERT
groups_remediationbatch     SELECT
groups_questionpreimage     SELECT          <- cannot alter its own undo
groups_questionapproval     (none)
groups_referencesolution    (none)
groups_oracleexecution      (none)
groups_codesubmission       (none)
```

It cannot append, edit or remove a pre-image, so it cannot damage the state
rollback would restore it to. And it has no privilege of any kind on approvals,
references, oracle executions or learner submissions.

**The two roles are exact mirrors**, which is the point:

| | `learnlm_remediate_rw` | `learnlm_hidden_test_rw` |
|---|---|---|
| `content` UPDATE | yes | **no** |
| `hidden_test_cases` UPDATE | **no** | yes |
| everything else on the table | no | no |

Statement-before-keys is now a privilege boundary. Neither role can violate the
order even if the operator or the code asked it to.

## D. Step 3 — batch and prior work intact

```
batch        p27-pilot-1   CAPTURED, frozen
membership   [17, 264, 266, 963, 1436, 1689, 3309]
pre-images   7/7 recompute to their recorded digests
q264         byte-identical to its pre-image   (SAFE control)
q17, q266    byte-identical to their pre-images
q963         at its post-repair digest 8da0eb14…7ab688
actions      exactly 1 — q963 STATEMENT_REPAIR, 2026-08-17 07:09:53
```

## E. Step 4 — baseline unmoved

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0

fingerprint 783354ae1d3a32056885706fbba3bc5ceaa9a01e5a84604cfbb93a6bfed086b5
```

Identical to the post-q963 baseline. Nothing drifted between phases.

## F. Step 5 — q17 dry-run

```
HIDDEN-TEST REPAIR  (DRY RUN)
  database        neondb
  role            learnlm_hidden_test_rw
  production      True
  operator        Suhas

  batch           p27-pilot-1 (CAPTURED)
  question        17 — Letter Combinations of a Phone Number
  pre-image       4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
  current digest  4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
  projected after 704a1652751f5043e2d75794544cd63ba740e25e07c0355a61faa89690893cb5
  field           hidden_test_cases (the ONLY field this command can change)
  cases           4 (unchanged count; stdin values are held fixed)

    case 1: unchanged   '["ad","ae","af","bd","be","bf","cd","ce","cf"]' (str)
    case 2: unchanged   '[]' (str)
    case 3: CHANGED   stdin '1'  (held fixed)
      before  []                 (list)
      after   '[]'               (str)
    case 4: CHANGED   stdin '9'  (held fixed)
      before  ['w', 'x', 'y', 'z']      (list)
      after   '["w","x","y","z"]'       (str)

DRY RUN — nothing was written.
```

**The projected digest is `704a1652…893cb5`, the value computed last phase
before the role existed** — the plan did not move when the connection changed.

Current digest equals the pre-image digest, so this repair starts from exactly
the state that was captured and frozen. Two of four cases change; both change
*type*, not answer. For `digits='9'` the letters are still w, x, y, z.

## G. Step 6 — q266 not touched

```
remediation/q266_approved_cases.json   270 bytes, 4 cases
  sha256  5f740b69409c545409358e3ecde5d44d479943983c638026ef2ce5d3c5366df0
q266 pre-image   1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
q266 live        1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
```

Unchanged. Its file exists and was not read by this run.

## H. Production after the dry-run

Re-measured rather than assumed — a dry-run that leaves a trace is not one:

```
q17 still at its pre-image   yes
remediation actions          1 (q963 only)
fingerprint                  783354ae…d086b5   unchanged
```

## I. Tests

```
174 passed — feature-flag/documentation guard, pooling guard,
             hidden-test repair, role contract, statement repair,
             operator gates, pre-image
```

The documentation guard is the one that matters here: it fails if code reads a
variable this repo has not documented, and it passes with the five new
`HIDDENTEST_*` entries.

## J. Next action — awaiting your word

Everything for q17 is verified and nothing is applied. On your instruction:

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py remediate_hidden_tests --alias hiddentest --batch p27-pilot-1 --question 17 --cases-file remediation/q17_approved_cases.json --reason "adjudication record: serialise two list values to canonical strings (form only)" --operator Suhas --apply --confirm
```

Then verify, and **stop before q266** — one at a time, with verification
between, because this is the first write to the column the grader compares
against and a surprise would show up on the first one.

---

```
HIDDEN-TEST ROLE  = VERIFIED, column-scoped to hidden_test_cases
q17               = DRY-RUN CLEAN, NOT APPLIED
q266              = UNTOUCHED, NOT DRY-RUN
q963 KEY_REPAIR   = NOT STARTED
BATCH             = FROZEN
PRODUCTION        = UNCHANGED (783354ae…d086b5)
```
