# P2.7 — q3309 migrated to v3, verified

**The first question in the bank to declare the v3 execution contract.** Its
four-step chain — statement, input, contract — is complete, every case binds
through the canonical adapter, and nothing else moved. q1436 was not touched.

---

## A. Role verification

`learnlm_contract_rw`, verified **as the role itself** through the dedicated
alias:

```
endpoint       ep-blue-hat-aj7p2x8v-pooler   (canonical: True)
database       neondb                        server 17.10 (29ad1b7)
current_user   learnlm_contract_rw
session_user   learnlm_contract_rw

LOGIN True · SUPERUSER False · CREATEDB False · CREATEROLE False
REPLICATION False · BYPASSRLS False · memberships NONE
```

Every column of `groups_question`:

```
table-level:  SELECT=T  INSERT=F  UPDATE=F  DELETE=F  TRUNCATE=F

execution_contract_version   T   <- the only one
id, title, content, base_difficulty, topic_id, hidden_test_cases,
boilerplate_code, hidden_wrapper_code, status, trust_state        all F
```

```
groups_remediationaction    SELECT, INSERT
groups_remediationbatch     SELECT
groups_questionpreimage     SELECT        <- cannot alter its own undo
groups_questionapproval     []
groups_referencesolution    []
groups_oracleexecution      []
groups_codesubmission       []
```

Neither over- nor under-granted. The other five aliases still connect as
`learnlm_census_ro`, `learnlm_preimage_rw`, `learnlm_remediate_rw`,
`learnlm_hidden_test_rw` and `learnlm_boilerplate_rw` — nothing was broadened.

**Six roles, six columns.** The remediation order is now a privilege boundary
end to end: no single role can repair a statement, a key, an input, a starter
and a contract.

## B. q3309 feasibility before the migration

```
live digest        1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
pre-image          2395b945…d47a0a   verifies
contract column    'v1'
statement repair   still present (both bounds relaxed)
input repair       still present (case 4 = ["",""])

case 1  OK  no warnings  ('hello', 'll')
case 2  OK  no warnings  ('aaaaa', 'bba')
case 3  OK  no warnings  ('abc', 'a')
case 4  OK  no warnings  ('', '')
case 5  OK  no warnings  ('mississippi', 'issip')
```

Five of five bind, each to exactly two strings, with no warnings. No `stdin` and
no `expected_output` change was proposed — this command writes one column and
holds no privilege on `hidden_test_cases` at all.

## C. The migration

Real-role dry-run, then applied:

```
current digest  1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
stored value    'v1'  (grades as v1)
proposed value  'v3'
declared        strStr(haystack: str, needle: str)   arity 2

case 4: stdin '["",""]'
        case_identity 439083f38956ba51…  (unchanged — this command cannot
                                          write hidden_test_cases)
        binds to  ('', '')      envelope  ["",""]
```

```
before digest   1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
after digest    8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6
```

## D. Post-digest and the exact change

The after-digest equals the approved plan's projection, computed before the role
existed.

```
total actions      9        CONTRACT_REPAIR actions: exactly 1
question           3309     batch p27-pilot-1     operator Suhas
post_digest        8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6
linked pre-image   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a

execution_contract_version   'v3'   — and the harness resolves it as v3
```

**Proof that nothing else moved on this question**: recomputing q3309's state
digest with the contract put back to `v1` reproduces
`1125858c…811c63fb`, the pre-contract digest, exactly. One field changed.

Against the frozen pre-image, three fields have now moved on q3309 — `content`
(statement repair), `hidden_test_cases` (input repair) and
`execution_contract_version` (this repair) — each by its own reviewed action.
`status`, `trust_state`, `boilerplate_code` and `hidden_wrapper_code` are
byte-identical to the capture.

The suite is untouched: stdin `['hello\nll\n', 'aaaaa\nbba\n', 'abc\na\n',
'["",""]', 'mississippi\nissip\n']`, expected `['2', '-1', '0', '0', '4']`.

## E. v3 adapter verification

```
case 1  OK  warnings=()  ['hello', 'll']            two strings
case 2  OK  warnings=()  ['aaaaa', 'bba']           two strings
case 3  OK  warnings=()  ['abc', 'a']               two strings
case 4  OK  warnings=()  ['', '']                   two strings
case 5  OK  warnings=()  ['mississippi', 'issip']   two strings
```

Exactly two typed arguments per case, both strings, no undeclared warnings, no
contract mismatch. Judge0 was not executed.

Worth stating what this does and does not mean: **q3309 is now executable as
declared.** Whether its stored answers are *right* is a separate question that
the oracle has not been asked, and its trust state says so.

## F. Whole-bank safety

```
q264, q1689   byte-identical to their pre-images
q963  8da0eb14…   q17  704a1652…   q266  4df2af27…   q1436  333e76c7…
batch         CAPTURED, frozen, 7 members

1. q963  STATEMENT_REPAIR    4. q3309 STATEMENT_REPAIR   7. q1436 INPUT_REPAIR
2. q17   HIDDEN_TEST_REPAIR  5. q1436 STATEMENT_REPAIR   8. q1436 BOILERPLATE_REPAIR
3. q266  HIDDEN_TEST_REPAIR  6. q3309 INPUT_REPAIR       9. q3309 CONTRACT_REPAIR
```

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0

declaring v3           1     q3309 — Find the Index of the First Occurrence…
outside v1/v3          0
```

**Exactly one question declares v3, and it is q3309.** Every other question in
the bank is still graded by the harness it has always been graded by — which was
the point of making v3 opt-in per question rather than a bulk migration.

```
prior     296627192435888cd8791bd2bfa2e73428cab8ae343bd34efb27a977cb6e6047
current   b1943363379af63e627b139940e58737b1b3f1c30bc60ede59b148e5f7b819ac
```

Recomputing the whole-bank fingerprint with q3309's contract put back to `v1`
reproduces the prior baseline exactly, so `q3309.execution_contract_version` is
the only field in the bank that moved.

**New baseline:**

```
b1943363379af63e627b139940e58737b1b3f1c30bc60ede59b148e5f7b819ac
```

## G. Regression

```
2294 passed, 0 failed, 0 errors     (groups, common, learning — 3m14s)
contract + adapter + contract-version suites   180 passed
contract-repair mutation                       23 killed / 24, 1 planted
                                               equivalent, 0 real survivors
```

## H. Rollback readiness — ready, not executed

```
pre-image verifies      yes
pre-image contract      'v1'
rollback target         2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
live == recorded post   true (no divergence)
batch                   frozen
status / trust          DRAFT / UNVERIFIED
```

Rolling q3309 back restores it to the state before all three of its repairs; the
pre-image predates every one. Rollback has still never run against real data.

## I. Exact next action

**q1436 → v3.** Same role, same command, and its blockers are cleared: all four
cases bind with zero warnings since the annotation landed.

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py remediate_contract --alias contract --batch p27-pilot-1 --question 1436 --to-version v3 --reason "approved plan: migrate to v3 now that the annotation makes every case bind to one list" --operator Suhas --apply --confirm
```

Projected digest `0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a`
(from the approved plan; I will recompute and dry-run before applying).

Not started.

---

```
q3309 CONTRACT_REPAIR = COMPLETE + VERIFIED
q1436 CONTRACT_REPAIR = READY, NOT APPLIED
q963 KEY_REPAIR       = NOT STARTED
ORACLE                = NOT STARTED
BATCH                 = FROZEN
RESEED                = NO
```
