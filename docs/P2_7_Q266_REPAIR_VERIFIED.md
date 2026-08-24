# P2.7 — q266 hidden-test repair verified

**The third production grading-truth write is complete and verified**, and with
it every *mechanical* repair in the frozen pilot. One field of one question
changed, proven across all 2,926 questions. Rollback was not executed.

All reads ran as `learnlm_census_ro` on `neondb`.

---

## 1. Remediation action

```
total actions      3
  q963   STATEMENT_REPAIR     2026-08-17 07:09:53  by Suhas
  q17    HIDDEN_TEST_REPAIR   2026-08-17 07:48:12  by Suhas
  q266   HIDDEN_TEST_REPAIR   2026-08-17 11:24:41  by Suhas

question           266
batch              p27-pilot-1
action_class       HIDDEN_TEST_REPAIR
operator           Suhas
detail             adjudication record: boolean casing True/False -> true/false
                   to match the wrapper (form only)
post_digest        4df2af2733546b7c208a0407f91254f36244a2361065f6827c14299825a8f704
linked pre-image   1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
```

The post-digest equals the digest projected in the dry-run — for the third time,
the write produced exactly what was previewed. The linked pre-image is
unchanged.

## 2. The exact change

Live q266 digest is `4df2af27…a8f704`, equal to the recorded post-digest.

```
content                      identical
status                       identical
trust_state                  identical
execution_contract_version   identical
boilerplate_code             identical
hidden_wrapper_code          identical
hidden_test_cases            CHANGED     <- the only one
```

Case count 4 → 4, every `stdin` byte-identical, every `case_identity` and
`input_identity` unchanged:

```
case 1  stdin 'code'      'False' -> 'false'
case 2  stdin 'aabbccee'  'True'  -> 'true'
case 3  stdin 'abcba'     'True'  -> 'true'
case 4  stdin 'carerac'   'True'  -> 'true'
```

Two independent checks that this is casing and nothing else:

- each written value equals `execution_adapter.canonical_output` of the boolean
  the stored text expressed — the contract's own printer, not a retyped
  equivalent;
- `old.lower() == new` holds for all four, so no character other than case moved.

`is_canonical_output` goes **False → True** on every case. Before the repair the
wrapper printed `true`/`false` while the key said `True`/`False`, so these four
cases could never be passed by a correct submission. They can now.

## 3. Other pilot questions

```
q264    byte-identical to pre-image    <- SAFE control
q1436   byte-identical to pre-image
q1689   byte-identical to pre-image
q3309   byte-identical to pre-image
q963    still at 8da0eb14…7ab688       (statement repair)
q17     still at 704a1652…893cb5       (hidden-test repair)
```

The control has now survived capture, freeze, and three live production writes
to sibling questions.

## 4. Batch

```
batch_key    p27-pilot-1
state        CAPTURED
frozen_at    2026-08-17 05:32:58 UTC
membership   [17, 264, 266, 963, 1436, 1689, 3309]   (7, unchanged)
actions      q17 + q266 HIDDEN_TEST_REPAIR, q963 STATEMENT_REPAIR — no others
```

## 5. Whole-bank impact

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to baseline. Nothing changed status or trust state, so
nothing became adaptive-eligible and no learner evidence moved. **q266 is a
repaired question, not a trusted one** — it is still DRAFT/UNVERIFIED, and
that is deliberate: the keys are now expressible, but nothing has executed them.

### The fingerprint, and the proof it is confined to q266

```
prior     0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
current   8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
```

Recomputing the whole-bank fingerprint with q266's `hidden_test_cases`
substituted back from its pre-image, every other value left exactly as
production holds it now:

```
q266 hidden_test_cases md5   473e188d… (before)  ->  d449dc36… (now)
reconstructed                0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
                             == the prior baseline, exactly
```

The substituted value is the pre-image column rendered by Postgres's own `jsonb`
output routine — the same one that renders the live column — so equal values
render to equal text and this is a comparison rather than a re-serialisation
guess.

Since the fingerprint covers six fields across **all 2,926 questions**, this
proves `q266.hidden_test_cases` is the only field in the entire bank that moved.

**New baseline for subsequent phases:**

```
8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
```

## 6. Rollback readiness — ready, not executed

```
pre-image verifies         yes
pre-image holds            ['False', 'True', 'True', 'True']   (the ORIGINAL)
live holds                 ['false', 'true', 'true', 'true']
restore target             1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
live == recorded post      true (no divergence)
batch                      frozen
```

**Rollback was not run.** It has still never executed against real data; its
first real invocation will be its first real test.

## 7. Where the pilot now stands

Three writes, three verifications, one field each. The mechanical repairs are
done; what remains needs judgement or execution, not another column grant.

```
q963 STATEMENT_REPAIR   = COMPLETE
q17  HIDDEN_TEST_REPAIR = COMPLETE
q266 HIDDEN_TEST_REPAIR = COMPLETE
q1436 CONTRACT_REPAIR   = NOT STARTED
q3309 CONTRACT_REPAIR   = NOT STARTED
q1689 MANUAL_REVIEW     = PENDING
KEY_REPAIR              = NOT STARTED   (q963, needs an oracle)
ORACLE                  = NOT STARTED
APPROVAL                = NOT STARTED
PROMOTION               = NOT STARTED
BATCH                   = FROZEN
RESEED                  = NO
```

Two things worth keeping in view, neither of them actionable without your
instruction:

- **q264, the control, has a known wrong key** (n=15 stored as 30, correct 24).
  It was left untouched on purpose so the batch had an unmoved reference; that
  defect is still recorded and still unrepaired.
- **q963's statement is now correct while its keys are not**, and they disagree
  visibly. That is the intended intermediate state until `KEY_REPAIR_AFTER_ORACLE`.
