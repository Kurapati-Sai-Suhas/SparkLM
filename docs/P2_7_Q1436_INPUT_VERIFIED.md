# P2.7 — q1436 input repair verified

**Both approved input repairs are now complete.** One `stdin` of one case of one
question changed, proven across all 2,926 questions. The pilot's mechanical and
input-level work is finished; what remains is the boilerplate annotation and the
two contract migrations.

All verification reads ran as `learnlm_census_ro`.

---

## Step 1 — pre-write verification

```
q1436 live        a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641
q1436 pre-image   0d442588…340508   verifies
batch             CAPTURED, frozen, 7 members
q264              byte-identical to its pre-image
q963 / q17 / q266 / q3309   all at their recorded digests
q1436 actions so far        ['STATEMENT_REPAIR']   — no prior INPUT_REPAIR
total actions               6
case 2 stdin                the cyclic input, expected_output 'D'
```

All eight preconditions held.

## Step 2 — dry-run

```
pre-image       0d442588…340508
current digest  a1187744…835641
projected after 4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
cases           4 (unchanged count and order; every expected_output held fixed)

  case 1: untouched
  case 2: STDIN CHANGED
    before stdin      '[["B","C"],["D","B"],["C","D"]]'
    after stdin       '[["B","D"],["A","B"],["C","D"]]'
    expected_output   'D'  UNCHANGED
    case identity     ca98a35032745542… -> 991a0528a61ec16f…
    output identity   3f39d5c348e5b79d… -> 3f39d5c348e5b79d…  UNCHANGED
  case 3: untouched
  case 4: untouched
```

## Step 3 — applied

```
before digest   a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641
after digest    4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
```

Equal to the projection computed two phases ago.

## Step 4 — post-write verification

### The action — exactly one new

```
total actions      7            q1436 INPUT_REPAIR actions: exactly 1

question           1436
action_class       INPUT_REPAIR
batch              p27-pilot-1
operator           Suhas
applied_at         2026-08-18 14:29:41
detail             approved plan: replace the cyclic case-2 input with the
                   approved acyclic graph
post_digest        4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
linked pre-image   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
```

### Fields

```
content                      CHANGED — the earlier statement repair, 2 characters
status                       identical to the pre-image
trust_state                  identical to the pre-image
execution_contract_version   identical to the pre-image   'v1'
boilerplate_code             identical to the pre-image
hidden_wrapper_code          identical to the pre-image
hidden_test_cases            CHANGED — this repair
```

`content` differing is expected and quantified: exactly the two characters of
the Example-2 sync, not a third.

### The suite

```
case 1  byte-identical  expected='Rome'  case_id same   output_id same
case 2  CHANGED         expected='D'     case_id MOVED  output_id same
case 3  byte-identical  expected='Z'     case_id same   output_id same
case 4  byte-identical  expected='D'     case_id same   output_id same

4 cases before, 4 after; order unchanged
case 2 stdin   [["B","C"],["D","B"],["C","D"]]  ->  [["B","D"],["A","B"],["C","D"]]
case 2 expected 'D'   unchanged
no expected_output changed anywhere
exactly one case identity moved; no output identity moved
```

**The statement's Example 2 and the graded case 2 now hold the same input** —
checked directly against the repaired statement text, not inferred. The
disagreement opened by the statement repair is closed.

### One thing that is still true

```
case 2 binding   OK, warnings=('undeclared_parameter_type',)
```

The input is now answerable, but `paths` is still unannotated, so the adapter
would guess its type. That is why `BOILERPLATE_REPAIR` comes before
`CONTRACT_REPAIR` for this question, and why the contract command still refuses
it.

## Step 5 — whole-bank safety

```
q264    byte-identical to pre-image    <- SAFE control
q1689   byte-identical to pre-image
q963    at 8da0eb14…7ab688     q17    at 704a1652…893cb5
q266    at 4df2af27…a8f704     q3309  at 1125858c…11c63fb

batch  p27-pilot-1  CAPTURED, frozen, 7 members

1. q963   STATEMENT_REPAIR      5. q1436  STATEMENT_REPAIR
2. q17    HIDDEN_TEST_REPAIR    6. q3309  INPUT_REPAIR
3. q266   HIDDEN_TEST_REPAIR    7. q1436  INPUT_REPAIR
4. q3309  STATEMENT_REPAIR
```

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to baseline.

```
prior     940380338b9c2e8b15ca89d480538c8f6b3e9b0609cd01d10ed83b839baa6c96
current   296627192435888cd8791bd2bfa2e73428cab8ae343bd34efb27a977cb6e6047
```

Recomputed with q1436's `hidden_test_cases` substituted back from its pre-image,
rendered by Postgres's own `jsonb` output routine:

```
q1436 hidden_test_cases md5   cf626dcd… (captured)  ->  cf6f534a… (now)
reconstructed                 940380338b9c2e8b15ca89d480538c8f6b3e9b0609cd01d10ed83b839baa6c96
                              == the prior baseline, exactly
```

`q1436.hidden_test_cases` is the only field in the bank that moved.

**New baseline:**

```
296627192435888cd8791bd2bfa2e73428cab8ae343bd34efb27a977cb6e6047
```

## Step 6 — rollback readiness — ready, not executed

```
pre-image verifies         yes
pre-image case 2 stdin     [["B","C"],["D","B"],["C","D"]]   the ORIGINAL cyclic input
rollback target            0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
live == recorded post      true (no divergence)
batch                      frozen
q1436 status / trust       DRAFT / UNVERIFIED
```

Rolling q1436 back would undo both the statement repair and this one, since the
pre-image predates both. Rollback was **not** run — it has still never executed
against real data.

---

```
q1436 INPUT_REPAIR       = COMPLETE + VERIFIED
q1436 BOILERPLATE_REPAIR = NOT STARTED
q3309 CONTRACT_REPAIR    = READY          (all five cases bind; needs the role)
q1436 CONTRACT_REPAIR    = READY AFTER BOILERPLATE
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```

Next in the approved order: build `remediate_boilerplate` and create
`learnlm_boilerplate_rw` for q1436's annotation, or create `learnlm_contract_rw`
and migrate q3309 — whose chain is already complete. Neither started.
