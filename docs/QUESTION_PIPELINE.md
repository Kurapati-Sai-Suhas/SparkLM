# The question pipeline and canonical readiness (QP1)

`groups/question_readiness.py` answers one question for every row in the bank:
**how ready is it for the SparkLM pipeline, and what is the next allowed
operation?** It is read-only, derived from facts that already exist, and is
the foundation the remediation milestones (QP2–QP5) build on.

```
python manage.py question_readiness                       # the whole bank
python manage.py question_readiness --questions 121 132 --json
python manage.py question_readiness --topic Array --category D
python manage.py question_readiness --out readiness.json  # full per-question JSON
```

The per-question JSON quotes stored inputs inside refusal details, so it is
grading data: keep `--out` files outside the repository.

## 1. The lifecycle

```
Raw Question
  -> Structural Validation        (servable, deliverable, starter, signature)
  -> Contract Validation          (execution contract version)
  -> Input / Answer Validation    (every case admitted in the contract language)
  -> Language Validation          (each served language binds every case)
  -> Execution / Reference        (operator-authored reference, Oracle runs)
  -> Hidden-Test Quality          (12+ cases, mutation gate)
  -> Human Review                 (QuestionApproval against the artifact digest)
  -> Trust                        (question_promote: ORACLE_VERIFIED)
  -> Publication                  (question_status: PUBLISHED)
  -> Adaptive Eligibility         (PUBLISHED and ORACLE_VERIFIED, per language)
```

The commands that move a question along it already exist: see
`groups/trust_coverage.py` (`TRUST_PIPELINE`) for the trust half and the
`remediate_*` commands for the repair half.

## 2. Readiness stages (derived, never stored)

The only STORED lifecycle is `Question.status` x `Question.trust_state` x
`Question.verified_language`. The stages below are a reading of facts that
live in other rows; QP1 adds no field, table or second trust system. A stage
is reached only when every earlier stage holds.

| Stage | Holds when | Read from |
|---|---|---|
| `UNAUTHORED` | content carries the placeholder marker, or there are no hidden tests | the two content filters of `_servable_questions()` |
| `AUTHORED` | real content and a non-empty suite | same |
| `STRUCTURALLY_VALID` | servable (deliverable), Python starter ready, a `Solution` method, every parameter annotated, a value is returned | `_servable_questions()`, `deliverability`, `language_readiness`, `suite_admission._graded_method` |
| `CHECKABLE` | no malformed case, and Python admits every case under the stored contract | `hidden_tests.validate_suite` / `is_gradable`, `suite_admission.check_case` |
| `REFERENCE_PRESENT` | an APPROVED and active reference exists | `ReferenceSolution` |
| `ORACLE_EVIDENCE` | at least one SUCCESS oracle execution is recorded (completeness is `question_review`'s check) | `OracleExecution` |
| `QUALITY_PASSED` | an approval froze a passing quality verdict | `QuestionApproval.quality_outcome` via `question_artifact.QualityOutcome` |
| `HUMAN_APPROVED` | an approval exists | `QuestionApproval` |
| `ORACLE_VERIFIED` | `trust_state == ORACLE_VERIFIED` | `Question` |
| `PUBLISHED` | `status == PUBLISHED` | `Question` |
| `ADAPTIVE_ELIGIBLE` | `is_adaptive_eligible` | `Question` |

`QUALITY_PASSED` and `HUMAN_APPROVED` first appear in the database together,
in one `QuestionApproval` row: a passing quality report nobody has approved is
a file, which a read-only report cannot see.

**Learner-state updates** happen only for a submission whose language passes
`adaptive_eligible_for(language)`: `ADAPTIVE_ELIGIBLE` and the language the
oracle verified. **Serving** is allowed from `AUTHORED` plus deliverable
(`_servable_questions()`), deliberately not status- or trust-gated: an
unverified question is practice, and its verdict never teaches the learner
model.

## 3. Categories A–I

Exactly one per question, chosen by deterministic precedence (section 4).

| Cat | Name | Qualifies | Allowed operation | Furthest stage the operation reaches |
|---|---|---|---|---|
| A | SAFE_AS_IS | every stage holds and the question is served | none; re-verify after any change | ADAPTIVE_ELIGIBLE |
| B | SAFE_AUTOMATED_REPAIR | the only Python refusals belong to a PROVEN transformation class: True/False answer respelling, or the M18 input representation (`input_repair.plan` succeeds) | proven class through pre-image, plan digest, all-or-none apply, post-check (QP2) | CHECKABLE (then D) |
| C | HUMAN_REVIEW | needs judgement: not authored, malformed suite, input semantics no rule can repair, a wrong-type answer key, a trust invariant broken, awaiting promotion or publication | operator-authored change or decision through an existing command | ADAPTIVE_ELIGIBLE, after human work |
| D | REFERENCE_REQUIRED | checkable in Python, missing the next trust artifact: reference, oracle evidence, suite expansion or a passing quality approval | `reference_create` -> `reference_review` -> `oracle_execute` -> suite expansion -> `quality_gate` -> `question_approve` -> `question_promote` -> `question_status` | ADAPTIVE_ELIGIBLE |
| E | CONTRACT_REPAIR | the starter is not ready, a parameter is unannotated, or the inputs bind only under v3/v2 | `remediate_boilerplate`, `declare_signature`, or `remediate_contract` (a contract change is a product decision) | CHECKABLE |
| F | LANGUAGE_REPAIR | Python is clean, but a SERVED JavaScript/Java harness mis-binds a case | repair that language's starter/contract, or withdraw the language | CHECKABLE (that language) |
| G | STRUCTURAL_REPAIR | undeliverable under its contract, with a v2 path (`SAFE_TO_MIGRATE` or blocked by a fixable starter/test contract) | `migrate_contract_v2` (reviewed) | CHECKABLE |
| H | QUARANTINE | in the quarantine registry, a structure no contract builds, an in-place (`-> None`) method, or an own wrapper the validators cannot model | withhold; nothing until a new contract or a human resolution | AUTHORED |
| I | DESIGN_FLAWED | no `Solution` method: a design-class question no harness can call | remove from serving, or redesign as authoring | AUTHORED |

B means the TRANSFORMATION is proven safe, not that the answer key is right.
Every B question still goes through D before it can be trusted.

## 4. Category precedence

First match wins (`question_readiness._category`):

1. quarantine registry -> **H**
2. a broken trust invariant -> **C**
3. every stage holds and served -> **A**
4. not authored -> **C**
5. no `Solution` class -> **I**
6. structurally undeliverable -> **G** (v2 path) or **H**
7. in-place -> **H**; own wrapper with refusals -> **H**
8. malformed suite -> **C**
9. starter not ready -> **E**; unannotated parameter -> **E**
10. Python refusals -> **B** (True/False respell, M18 input), **C** (answer key form, input semantics) or **E** (v3/v2 admit)
11. a served language mis-binds -> **F**
12. missing trust artifact -> **D**
13. awaiting promotion or publication -> **C**

Quarantine precedes trust so that a quarantined question can never read as
safe, and the trust invariant precedes A so a stored `ORACLE_VERIFIED` without
the evidence behind it is surfaced instead of trusted. Structural checks
precede suite checks because that is the pipeline's order; a question with
both an unannotated signature and a malformed suite is E, and the suite
problem becomes the next blocker after the signature is repaired.

## 5. Blocker reason codes

Each report lists the blockers that stop the NEXT stage (plus advisory ones
that do not cap the stage). Codes live in `question_readiness.BLOCKER_CODES`.

| Code | Stops | Meaning |
|---|---|---|
| `NOT_AUTHORED_PLACEHOLDER`, `NO_HIDDEN_TESTS` | AUTHORED | the content pipeline has not armed it |
| `STRUCTURAL_UNDELIVERABLE` | STRUCTURALLY_VALID | `deliverability.blocker` (cause in the detail) |
| `NO_SOLUTION_CLASS`, `PYTHON_NOT_READY`, `NO_GRADED_METHOD` | STRUCTURALLY_VALID | the Python starter cannot be called as declared |
| `UNANNOTATED_PARAMETER`, `IN_PLACE_RETURN` | STRUCTURALLY_VALID | the signature declares no type, or no value |
| `MALFORMED_SUITE`, `OWN_WRAPPER` | CHECKABLE | the suite or its harness cannot be checked |
| `PYTHON_INPUT_REFUSED`, `PYTHON_ANSWER_FORM_REFUSED` | CHECKABLE | `suite_admission` refuses a case in Python |
| `LANGUAGE_BINDING_REFUSED` | advisory | a served language mis-binds; Python trust is unaffected |
| `DUPLICATE_CASES`, `BELOW_MIN_TESTS` | advisory (QUALITY_PASSED) | the quality gate will refuse until fixed |
| `NO_APPROVED_REFERENCE`, `NO_ORACLE_EVIDENCE`, `NO_PASSING_QUALITY_APPROVAL` | the trust ladder | the next trust artifact is missing |
| `NOT_ORACLE_VERIFIED`, `NOT_PUBLISHED`, `NOT_ADAPTIVE_ELIGIBLE` | the stored state | the next operator decision |
| `QUARANTINED` | — | in `content_quarantine` |
| `TRUST_INVARIANT_BROKEN` | — | stored trust the derived evidence does not support |

Each report also carries an `evidence` block (approved references, oracle
successes, approvals) that is reported whatever the stage, so a question whose
inputs do not bind still shows the trust artifacts it holds.

## 6. status, trust_state, verified_language and adaptive eligibility

- `status` says whether a question is available (DRAFT, PENDING_REVIEW,
  PUBLISHED; BLOCKED has no edges yet). It does not gate serving.
- `trust_state` says whether its answers have been proven (UNVERIFIED,
  ORACLE_VERIFIED). Only `question_promote` writes it.
- `verified_language` records WHICH language the oracle ran in; it is set
  exactly when `trust_state` is ORACLE_VERIFIED.
- `is_adaptive_eligible` = PUBLISHED and ORACLE_VERIFIED.
  `adaptive_eligible_for(language)` additionally requires the submission
  language to be the verified one. QP1 reports both and re-derives neither.

## 7. Which validator owns which decision

| Decision | Owner |
|---|---|
| Servable | `coding_views._servable_questions()` |
| Structurally deliverable | `deliverability.blocker` / `undeliverable_ids` |
| Language readiness (5 languages; C/C++ self-contained) | `language_readiness.assess` |
| Case admission, input and answer form | `suite_admission.check_case` |
| Suite well-formedness and floor | `hidden_tests.validate_suite`, `is_gradable`, `MIN_HIDDEN_TESTS` |
| v2 structural path | `migration_readiness.classify` |
| v1 input repair feasibility | `input_repair.plan` |
| Quarantine | `content_quarantine` |
| Quality verdict | `question_artifact.QualityOutcome` |
| Adaptive eligibility | `Question.is_adaptive_eligible`, `adaptive_eligible_for` |

QP1 owns only the stage order and the category precedence.

## 8. Read-only, and how that is enforced

1. The command runs the census connection gates first: against production the
   role must hold no write privilege, or nothing is read.
2. A statement guard is installed for the whole run; any statement that is not
   a read aborts the command.
3. The module has no write path, and the tests prove the database is unchanged
   after a full run.

`BankContext.load` reads everything that lives in other rows in a fixed
handful of queries, so `assess` issues none and the cost does not grow with
the number of questions. Measured on the production bank (2026-10-01):
2,926 questions, 6 queries in total, about 23 seconds.

## 9. What QP1 does NOT do

- It does not write, repair, promote, publish or quarantine anything.
- It does not execute code or call Judge0; execution evidence is read from
  `OracleExecution`, never produced.
- It does not judge whether an answer key is CORRECT. Only the oracle, run
  against an operator-authored reference, can.
- It does not change serving. `_servable_questions()` is unchanged.
- It does not enforce operator policy on individual questions (for example the
  questions the remediation commands refuse by name); write commands do.
- Its categories describe the next allowed operation, not a promise that the
  operation will succeed.

## 10. Baseline (production, 2026-10-01)

| Category | Count | | Stage | Count |
|---|---:|---|---|---:|
| A | 6 | | UNAUTHORED | 1,138 |
| B | 195 | | AUTHORED | 554 |
| C | 1,380 | | STRUCTURALLY_VALID | 621 |
| D | 598 | | CHECKABLE | 606 |
| E | 469 | | REFERENCE_PRESENT | 1 |
| F | 9 | | ADAPTIVE_ELIGIBLE | 6 |
| G | 108 | | | |
| H | 96 | | | |
| I | 65 | | | |

Servable 1,601; adaptive-eligible 6; questions below the 12-case floor among
the checkable: 606. These numbers are evidence of the day, not targets, and
nothing in the code depends on them.
