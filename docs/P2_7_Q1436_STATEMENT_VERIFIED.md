# P2.7 — q1436 statement repair verified

**Step 2 of the approved plan is complete.** Two characters of one field of one
question changed, proven across all 2,926 questions. Both statement repairs in
the plan are now done; nothing else was started.

---

## Step 1 — pre-write check (census)

```
q1436 live        0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
q1436 pre-image   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508   verifies
batch             CAPTURED, frozen, 7 members
q264              unchanged
q963  8da0eb14…   q17  704a1652…   q266  4df2af27…   q3309  bbd46d58…   all at digest
actions           4
fingerprint       596c057847ce928362818c8e2c7618f9d9f9be090836ad4c472337e60f93873d
```

All nine preconditions held.

## Step 2 — the approved statement file

`remediation/q1436_approved_statement.txt` — 1048 bytes, sha256
`e33e2488b2c279ec22d1985c018b5e0eb429b71c41edb89093af428f433f2217`.

Derived from the frozen pre-image by **one** replacement, asserted to match
exactly once (and the new text asserted absent beforehand, so the edit could not
land twice). Checked before the file was written:

```
length                1048 -> 1048
characters differing  2      offset 858 'C'->'D', offset 864 'D'->'A'
Output 2 kept         **Output 2:** `D`
Explanation kept      There are multiple paths but all of them end at D
Examples 1 and 3      byte-identical
text before/after the replaced span   byte-identical
projected digest      a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641
                      == the signed-off plan
```

## Step 3 — dry-run

```
pre-image       0d442588…340508
current digest  0d442588…340508      (starting from the capture)
field           content (the ONLY field this command can change)
size            1048 -> 1048 bytes
```

```diff
@@ -8,3 +8,3 @@
 
-**Input 2:** `[["B","C"],["D","B"],["C","D"]]`
+**Input 2:** `[["B","D"],["A","B"],["C","D"]]`
 **Output 2:** `D`
```

One hunk, one line. The `Output 2` line appears as unchanged context directly
beneath the change — the diff itself is the evidence it was not touched.

## Step 4 — applied

```
before digest   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
after digest    a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641
```

Equal to the projection computed before the file existed.

## Step 5 — verification

### The action — exactly one new

```
total actions      5
  q963   STATEMENT_REPAIR     2026-08-17 07:09:53
  q17    HIDDEN_TEST_REPAIR   2026-08-17 07:48:12
  q266   HIDDEN_TEST_REPAIR   2026-08-17 11:24:41
  q3309  STATEMENT_REPAIR     2026-08-18 09:05:18
  q1436  STATEMENT_REPAIR     2026-08-18 09:10:36   <- new

question           1436
action_class       STATEMENT_REPAIR
operator           Suhas
post_digest        a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641
linked pre-image   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
```

### The exact change

```
content                      CHANGED     <- the only one
status                       identical
trust_state                  identical
execution_contract_version   identical
boilerplate_code             identical
hidden_wrapper_code          identical
hidden_test_cases            identical
```

```
characters changed  2     offset 858 'C' -> 'D',  offset 864 'D' -> 'A'
length              1048 -> 1048
```

Verified present afterwards: the new Input 2, `**Output 2:** \`D\``, the
explanation verbatim, Examples 1 and 3, the input-format line and the
constraints line. Verified absent: the old cyclic input, anywhere in the
statement.

### The intended intermediate state

```
statement Example 2   [["B","D"],["A","B"],["C","D"]]     repaired
hidden case 2 stdin   [["B","C"],["D","B"],["C","D"]]     UNREPAIRED
they agree yet        False        <- INPUT_REPAIR not started
paths annotation      (none)       <- BOILERPLATE_REPAIR not started
contract column       'v1'         <- CONTRACT_REPAIR not started
```

**q1436's statement now shows a valid example while the graded case is still the
cyclic one.** That is the expected state between step 2 and step 4 of the plan,
and it is worth naming plainly: for the moment the learner reads a correct
example and would be graded against an input that has no destination city.
Nothing about the question is *newly* wrong — the hidden case was always the
defect — but the disagreement is now visible from the statement's side, and it
closes when `INPUT_REPAIR` lands.

### The other six

```
q264    byte-identical to pre-image    <- SAFE control
q1689   byte-identical to pre-image
q17     at 704a1652…893cb5
q266    at 4df2af27…a8f704
q963    at 8da0eb14…7ab688
q3309   at bbd46d58…3bc2cd
```

The control has survived capture, freeze and five live production writes.

### Batch

```
CAPTURED, frozen, membership [17, 264, 266, 963, 1436, 1689, 3309]
```

### Whole-bank impact

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to baseline.

```
prior     596c057847ce928362818c8e2c7618f9d9f9be090836ad4c472337e60f93873d
current   ef9c8f28ad089836bd76a6f2ac41c9356d5c62d1c2fd65308d56a97c3d321dbb
```

Recomputed with q1436's content substituted back from its pre-image, everything
else exactly as production holds it now:

```
q1436 content md5   78527133… (before)  ->  09b84621… (now)
reconstructed       596c057847ce928362818c8e2c7618f9d9f9be090836ad4c472337e60f93873d
                    == the prior baseline, exactly
```

`q1436.content` is the only field in the bank that moved.

**New baseline:**

```
ef9c8f28ad089836bd76a6f2ac41c9356d5c62d1c2fd65308d56a97c3d321dbb
```

### Rollback readiness — ready, not executed

```
pre-image verifies         yes
pre-image holds            the ORIGINAL cyclic example
restore target             0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
live == recorded post      true (no divergence)
```

Rollback was **not** run.

---

```
q1436 STATEMENT_REPAIR   = COMPLETE
q1436 INPUT_REPAIR       = NOT STARTED
q1436 BOILERPLATE_REPAIR = NOT STARTED
q1436 CONTRACT_REPAIR    = BLOCKED
q3309 STATEMENT_REPAIR   = COMPLETE
q3309 INPUT_REPAIR       = NOT STARTED
q3309 CONTRACT_REPAIR    = BLOCKED
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```

Next in the approved order: **migration 0045** (`CLASS_INPUT_REPAIR`, state-only)
and then building `remediate_inputs`. Neither started.
