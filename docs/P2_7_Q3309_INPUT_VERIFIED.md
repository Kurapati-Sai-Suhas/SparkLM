# P2.7 — Migration 0045 applied, q3309 input repair verified

**The first INPUT_REPAIR in the system is complete and verified.** One `stdin`
of one case of one question changed, proven across all 2,926 questions. q1436
was not touched.

All verification reads ran as `learnlm_census_ro`.

---

## A. Migration 0045

```
endpoint     ep-blue-hat-aj7p2x8v-pooler        database  neondb
server       PostgreSQL 17.10 (29ad1b7)

[X] 0044_pre_image_rollback              2026-08-16 18:52:19
[X] 0045_input_repair_action_class       2026-08-18 14:16:41

rows for 0045        exactly 1
rows beyond 0045     none
depends on           [('groups', '0044_pre_image_rollback')]
operations           ['AlterField']
```

Vocabulary now, in order:

```
CONTRACT_REPAIR · STATEMENT_REPAIR · BOILERPLATE_REPAIR · HIDDEN_TEST_REPAIR
EXPECTED_OUTPUT_REPAIR · INPUT_REPAIR · MANUAL_REVIEW · COMPLETE_REBUILD
· ROLLBACK
```

All eight pre-existing classes are still present; `INPUT_REPAIR` is the only
addition. The column itself is untouched — `information_schema` reports
`character varying(32)`, exactly as before, which is the observable proof that a
state-only migration ran.

**0045 changed no grading truth**, and that is shown rather than asserted: the
whole-bank fingerprint moves only by q3309's `hidden_test_cases`, and
substituting the captured suite back reproduces the prior baseline exactly
(§E). A migration that had touched any question row would break that
reconstruction.

## B. q3309 INPUT_REPAIR

```
total actions      6            INPUT_REPAIR actions: exactly 1

question           3309
action_class       INPUT_REPAIR
batch              p27-pilot-1
operator           Suhas
applied_at         2026-08-18 13:59:59
detail             approved plan: canonical v3 form for the
                   empty-haystack/empty-needle case
post_digest        1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
linked pre-image   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
```

Live digest equals the recorded post-digest, which equals the digest projected
in the dry-run.

**One correction to the brief.** The expected digest as written there is
`1125858c76abea1d22b93d1d0491e84ca46d09deacee273194cdfa811c63fb` — 62
characters, where a sha256 is 64. It is missing `6d` after `…b93d1d`. The stored
and live value is the full `1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb`,
matching the approved plan. A transcription slip in the brief, not a mismatch in
production.

### The suite, case by case

```
case 1  byte-identical  stdin='hello\nll\n'           expected='2'   case_id same   output_id same
case 2  byte-identical  stdin='aaaaa\nbba\n'          expected='-1'  case_id same   output_id same
case 3  byte-identical  stdin='abc\na\n'              expected='0'   case_id same   output_id same
case 4  CHANGED         stdin='["",""]'               expected='0'   case_id MOVED  output_id same
case 5  byte-identical  stdin='mississippi\nissip\n'  expected='4'   case_id same   output_id same
```

```
5 cases before, 5 after; order unchanged
case 4 stdin      '\n\n'  ->  '["",""]'
case 4 expected   '0'      unchanged
no expected_output changed anywhere
exactly one case identity moved — case 4, because its input changed
no output identity moved
case 4 now binds  ["",""]   -> ('', '')
```

The case that could not be expressed at all can now be expressed. It is still
not *graded* — the contract is still v1 (§C).

## C. q3309 field level

Against the frozen pre-image:

```
content                      CHANGED     — the approved statement repair, 2 characters
status                       identical
trust_state                  identical
execution_contract_version   identical   'v1'
boilerplate_code             identical
hidden_wrapper_code          identical
```

`content` differing is expected and quantified: exactly the two characters of the
earlier constraint repair, not a third. Everything except `content` and
`hidden_test_cases` is byte-identical to what was captured before any repair.

`execution_contract_version` is **'v1'** — the migration to v3 has not happened
and is not authorised.

## D. Other pilot questions

```
q264    byte-identical to pre-image    <- SAFE control
q1689   byte-identical to pre-image
q963    at 8da0eb14…7ab688
q17     at 704a1652…893cb5
q266    at 4df2af27…a8f704
q1436   at a1187744…835641             <- untouched by this phase
```

```
batch  p27-pilot-1  CAPTURED, frozen, 7 members

1. q963   STATEMENT_REPAIR
2. q17    HIDDEN_TEST_REPAIR
3. q266   HIDDEN_TEST_REPAIR
4. q3309  STATEMENT_REPAIR
5. q1436  STATEMENT_REPAIR
6. q3309  INPUT_REPAIR
```

Exactly the expected sequence, and nothing else.

## E. Whole-bank fingerprint

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to baseline.

```
prior     ef9c8f28ad089836bd76a6f2ac41c9356d5c62d1c2fd65308d56a97c3d321dbb
current   940380338b9c2e8b15ca89d480538c8f6b3e9b0609cd01d10ed83b839baa6c96
```

Recomputed with q3309's `hidden_test_cases` substituted back from its pre-image
— rendered by Postgres's own `jsonb` output routine, the same one that renders
the live column:

```
q3309 hidden_test_cases md5   e08f0278… (captured)  ->  b2e5d094… (now)
reconstructed                 ef9c8f28ad089836bd76a6f2ac41c9356d5c62d1c2fd65308d56a97c3d321dbb
                              == the prior baseline, exactly
```

Since the fingerprint covers `id`, `hidden_test_cases`, `content`, `status`,
`trust_state` and `execution_contract_version` for all 2,926 questions, this
proves `q3309.hidden_test_cases` is the only field in the bank that moved —
which is simultaneously the proof that **migration 0045 changed no question
row**.

**New baseline:**

```
940380338b9c2e8b15ca89d480538c8f6b3e9b0609cd01d10ed83b839baa6c96
```

## F. Rollback readiness — ready, not executed

```
pre-image verifies         yes
pre-image case 4 stdin     '\n\n'      the ORIGINAL, unrepaired input
rollback target            2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
live == recorded post      true (no divergence)
batch                      frozen
```

Rolling back would restore both this repair and the statement repair, since the
pre-image is the state before either. Rollback was **not** run.

## G. Production safety

```
remediation actions   6, all expected
question approvals    0
oracle executions     20   (unchanged)
reference solutions   1    (unchanged)
code submissions      44   (unchanged), adaptive_eligible 0
q3309 status/trust    DRAFT / UNVERIFIED
q3309 adaptive        False
```

No approval, no oracle run, no reference, no learner-visible state change. The
repaired question is more *expressible* than it was; it is no more *trusted*.

## H. Exact next action

**q1436 INPUT_REPAIR**, already dry-run and unchanged since:

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py remediate_inputs --alias hiddentest --batch p27-pilot-1 --question 1436 --changes-file remediation/q1436_case2_input.json --reason "approved plan: replace the cyclic case-2 input with the approved acyclic graph" --operator Suhas --apply --confirm
```

Projected digest `4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887`.
Not applied.

---

```
0045                     = APPLIED + VERIFIED
q3309 INPUT_REPAIR       = COMPLETE + VERIFIED
q1436 INPUT_REPAIR       = READY, NOT APPLIED
q1436 BOILERPLATE_REPAIR = NOT STARTED
CONTRACT_REPAIR          = BLOCKED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```
