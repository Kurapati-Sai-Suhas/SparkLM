# P2.7 — q17 hidden-test repair verified

**The second production grading-truth write is complete and verified.** Exactly
one field of exactly one question changed, proven across all 2,926 questions
rather than asserted. q266 was not touched. Rollback was not executed.

All reads below ran as `learnlm_census_ro` on `neondb`.

---

## 1. Remediation action

```
total actions      2
  q963   STATEMENT_REPAIR     2026-08-17 07:09:53  by Suhas
  q17    HIDDEN_TEST_REPAIR   2026-08-17 07:48:12  by Suhas

question           17
batch              p27-pilot-1
action_class       HIDDEN_TEST_REPAIR
operator           Suhas
detail             adjudication record: serialise two list values to canonical
                   strings (form only)
post_digest        704a1652751f5043e2d75794544cd63ba740e25e07c0355a61faa89690893cb5
linked pre-image   4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
```

The post-digest is **the value projected in the dry-run before the write** — the
apply produced exactly what was previewed. The linked pre-image is unchanged and
still the immutable before-state.

## 2. The exact change

Live q17 digest is `704a1652…893cb5`, equal to the recorded post-digest.

Field by field against the pre-image:

```
content                      identical
status                       identical
trust_state                  identical
execution_contract_version   identical
boilerplate_code             identical
hidden_wrapper_code          identical
hidden_test_cases            CHANGED     <- the only one
```

Case count 4 → 4. Every `stdin` byte-identical:

```
case 1  stdin '23'  unchanged   '["ad","ae","af","bd","be","bf","cd","ce","cf"]' (str)  unchanged
case 2  stdin ''    unchanged   '[]' (str)  unchanged
case 3  stdin '1'   unchanged   []                 (list) -> '[]'                (str)
case 4  stdin '9'   unchanged   ['w','x','y','z']  (list) -> '["w","x","y","z"]' (str)
```

Each new string was checked to equal `execution_adapter.canonical_output` of the
captured value — the execution contract's own renderer applied to the pre-image,
not a retyped equivalent.

### Identities — and one worthwhile detail

```
case_identity    unchanged  (all 4)
input_identity   unchanged  (all 4)
output_identity  unchanged  (all 4)
```

The **output** identity is unchanged even for cases 3 and 4, and that is not an
oversight in the check — it is the strongest available evidence that this repair
was form-only. `pre_image._repr_for_identity` already digested the stored list
through the same compact JSON rendering the repair wrote, so the digested
content is identical before and after. The repair changed how the answer is
*stored*, not what the answer *is*.

To be precise about what that does and does not mean: the state digest still
moved (`4dd7a168…` → `704a1652…`), because `state_digest` frames the raw field
value as well as the case identities. The identity layer is stable across the
change; the state digest is not. Both are behaving as designed.

## 3. The other six pilot questions

```
q264    byte-identical to pre-image    <- SAFE control
q266    byte-identical to pre-image
q1436   byte-identical to pre-image
q1689   byte-identical to pre-image
q3309   byte-identical to pre-image
q963    still at its statement-repair digest 8da0eb14…7ab688
```

The control has now survived capture, freeze, and two live production writes to
sibling questions.

## 4. Batch

```
batch_key    p27-pilot-1
state        CAPTURED
frozen_at    2026-08-17 05:32:58 UTC
membership   [17, 264, 266, 963, 1436, 1689, 3309]   (7, unchanged)
actions      q17 HIDDEN_TEST_REPAIR, q963 STATEMENT_REPAIR — and no others
```

## 5. Grading-truth status

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to the pre-repair baseline. No question changed status or
trust state, so nothing became adaptive-eligible and no learner evidence moved.

### The fingerprint, and the proof it is confined to q17

```
prior     783354ae1d3a32056885706fbba3bc5ceaa9a01e5a84604cfbb93a6bfed086b5
current   0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
```

A changed fingerprint says only "something moved". To show *what*, I recomputed
the whole-bank fingerprint with q17's `hidden_test_cases` substituted back from
its pre-image, leaving every other value exactly as production holds it now:

```
q17 hidden_test_cases md5   9bda474c… (before)  ->  63ca3a9f… (now)
reconstructed               783354ae1d3a32056885706fbba3bc5ceaa9a01e5a84604cfbb93a6bfed086b5
                            == the prior baseline, exactly
```

The substituted value is the pre-image column rendered by Postgres's own `jsonb`
output routine, the same one that renders the live column — so equal values
render to equal text and this is a comparison rather than a re-serialisation
guess.

Since the fingerprint covers `id`, `hidden_test_cases`, `content`, `status`,
`trust_state` and `execution_contract_version` for **all 2,926 questions**, this
proves `q17.hidden_test_cases` is the only field in the entire bank that moved —
not merely the only one among the seven in the batch.

**New baseline for subsequent phases:**

```
0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
```

## 6. Rollback readiness — ready, not executed

```
pre-image verifies         yes (recomputes to its recorded digest)
pre-image holds            the ORIGINAL suite — types ['str','str','list','list']
live now                   types ['str','str','str','str']
restore target             4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
live == recorded post      true (no divergence since the repair)
batch                      frozen
```

The immutable pre-image still holds the two unserialised lists, so q17 can be
restored to `4dd7a168…d4213a` at any time. **Rollback was not run** — it has
still never executed against real data, and its first real invocation will be
its first real test.

## 7. Status

Nothing was done beyond q17's hidden tests.

```
q963 STATEMENT_REPAIR  = COMPLETE
q17 HIDDEN_TEST_REPAIR = COMPLETE
q266 HIDDEN_TEST_REPAIR = NOT STARTED   (approved, dry-run not yet re-run)
KEY_REPAIR             = NOT STARTED
ORACLE                 = NOT STARTED
APPROVAL               = NOT STARTED
PROMOTION              = NOT STARTED
BATCH                  = FROZEN
RESEED                 = NO
```

q266 remains byte-identical to its pre-image and its approved case file
(`5f740b69…36df0`, 4 cases) is untouched. Nothing further will happen to it
without your instruction.
