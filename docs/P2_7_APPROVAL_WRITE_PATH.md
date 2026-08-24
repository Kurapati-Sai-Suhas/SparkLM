# P2.7 — Oracle evidence verified, approval write path built

q3309 has real oracle provenance: **24 executions, 12 cases, 2 agreeing runs
each, all SUCCESS**, every one naming reference #2 at its current source hash
under contract v3. `question_review` now runs read-only against production and
says, in its own words, **"No blockers. This artifact is approvable."**

Nothing was approved and nothing was promoted. q3309 is still `DRAFT /
UNVERIFIED`, `is_adaptive_eligible=False`, `QuestionApproval` count **0**.

The reason this phase existed at all: `question_review`, `question_approve` and
`question_promote` were written in P2.7g-3, **before the operator aliases**.
They used default managers and a bare `transaction.atomic()`. On this deployment
`default` is `learnlm_census_ro` — read-only — so the first real approval would
have failed at the database, and on any deployment where `default` is writable
they would have written the trust state through a connection with no
column-level limits at all.

---

## A. Oracle evidence — real, and bound to the final state

Read as `learnlm_census_ro`.

| property | value |
|---|---|
| question digest | `ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e` |
| canonical reference | #2, `18ad8f390642c315ef78e52fecaf97ab31fd6fc69fca2a02b3114e03d77a341b` |
| executions recorded | 24 |
| statuses | `{'SUCCESS': 24}` |
| wrong question / reference / stale hash / wrong contract | 0 / 0 / 0 / 0 |
| operator + limits recorded | yes, on every row |
| cases covered | 12 / 12, 2 runs each (`REQUIRED_AGREEING_RUNS`) |
| conflicting outputs within a case | 0 |
| executions naming a case outside the final suite | 0 |
| executions predating the SUITE_EXPANSION | 0 |
| recomputed reconciliation | `{'AGREEMENT': 12}` |
| `collect_case_evidence` | 12 rows, **no blockers** |

Each case was checked three ways, not one: the recorded `input_digest` equals
`provenance.input_identity(case["stdin"])`, the recorded `output_digest` equals
the identity of the **stored** `expected_output`, and `reconcile_case` was
re-run from the produced output. The suite's own expansion is used as a clock —
no execution predates it — so no row can be evidence for a case that has since
changed.

## B. The quality artifact, and what it does *not* claim

`reports/q3309-quality.json` binds question `3309`, state digest `ebb26e7f…`,
contract `v3`, reference `#2` at hash `18ad8f39…`, and all twelve case
identities in order. Tier 1 = **1.0**, tier 2 = **1.0**, no blockers, verdict
**PASS**.

The report was written **before** the first oracle execution. That is correct
and worth stating plainly: the gate and the oracle are independent evidence.
The gate proves the suite *discriminates*; the oracle proves the answer key is
*produced by a canonical implementation*. The report carries kill rates and
mutant identities only — it claims no oracle provenance, so nothing about it is
stale. What binds them together is the state digest, which both name.

## C. What the three commands were doing wrong

None of the three accepted `--alias`. Verified before changing anything:

```
question_review   alias in options: False
question_approve  alias in options: False
question_promote  alias in options: False
```

`question_promote` is the **only writer of `Question.trust_state` in the
codebase**. It was writing it through `default`.

## D. The routing repair

All three now take `--alias`, and the alias reaches every read and every write:

```python
question = trust.resolve_question(options["question"], alias)   # the read
with transaction.atomic(using=alias):                            # the transaction
    approval.save(using=alias)                                   # the write
    question.save(using=alias, update_fields=["trust_state"])
```

`_question_trust.resolve_question` gained the alias and routes with
`Question.objects.using(alias)`. Foreign keys are assigned **by id**
(`approved_by_id`, `reviewed_by_id`, `executed_by_id`, `promoted_by_id`)
because the operator is read through `default` while the row is written through
the operator alias, and Django refuses a cross-alias object assignment.

`question_review` remains read-only, unconditionally. It takes an alias so it
can *read* production, and it holds no write gate because it opens no
transaction.

## E. Two roles, deliberately unable to do each other's job

The lifecycle's whole value is that the human who vouches and the actor who
enacts are not the same principal. That is now a database property, not a
convention:

```python
ALLOWED_APPROVAL_ROLES  = {"learnlm_approve_rw"}
ALLOWED_PROMOTION_ROLES = {"learnlm_promote_rw"}

APPROVAL_PROBE  = (("groups_questionapproval", None, "INSERT"),)
PROMOTION_PROBE = (("groups_question", "status", "UPDATE"),
                   ("groups_question", "trust_state", "UPDATE"))
```

`APPROVAL_FORBIDDEN` denies the approver any `groups_question` INSERT/UPDATE —
so an approver **cannot promote**. `PROMOTION_FORBIDDEN` denies the promoter
`groups_questionapproval` INSERT and every reference/execution write — so a
promoter **cannot author the approval it then acts on**, and cannot forge the
oracle evidence underneath it. The role lists are disjoint and no existing
remediation role was broadened.

Both gates are skipped on non-production targets and the privilege probe still
runs, so the tests exercise the real `has_column_privilege` path.

## F. Tests

`groups/test_approval_write_path.py` — **27 tests, all passing**. Real
Postgres roles where the property is a privilege; AST where the property is
"no code path reaches the default connection", because in a test every alias
resolves to the same database and no single run can distinguish them.

Covered: all three commands accept an alias; the read is routed; the helper
routes it; both writers pass it; no `save()`/`atomic()` in any of the three
lacks the alias; an approver denied `UPDATE groups_question`; a promoter denied
`INSERT groups_questionapproval`; the artifact digest must round-trip;
`--confirm` required on both; blockers refuse approval; a promotion with no
approval refuses.

## G. Mutation testing — 24 killed / 25, 0 real survivors

25 mutants across routing (7), gates (8), evidence rules (9) and one planted
equivalent.

The first sweep left **three real survivors**, all the same defect in the test
suite rather than the code: `M1` (the helper stops routing), `M6` and `M7`
(approve/promote stop passing the alias to the read). The routing test
monkeypatched `resolve_question` for the **review** path only, and the AST tests
checked `save`/`atomic` keywords but never the read. Two tests closed them:
`resolve_question` must contain `.using(alias)`, and every
`trust.resolve_question(...)` call site must pass two arguments — the helper
defaults to `default`, so a forgotten argument reads production through the
census connection while writing somewhere else.

Final sweep:

```
24 killed / 25
real survivors: 0
  E1  EQUIVALENT: hyphenation in a docstring
```

## H. Regression

```
2435 passed, 2 warnings in 195.58s
```

## I. Production state after the phase — unchanged

```
q3309   digest ebb26e7f…  cases 12  v3  DRAFT/UNVERIFIED  adaptive=False
        OracleExecution 24   QuestionApproval 0
q1436 / q963 / q17            unchanged
q264 / q266 / q1689           at pre-image
RemediationAction 12   ReferenceSolution 2

fingerprint 9cc3c8d8b0d050d0cebb6a43a44adbb68612d2e5077129c915481c1b66715acf
            == expected: True
```

The whole-bank fingerprint (6 fields × 2,926 questions) is identical to the one
recorded after the suite expansion. This phase wrote nothing to production.

## J. The review, run for real

`question_review --question 3309 --alias oracle --operator Suhas
--quality-report reports/q3309-quality.json`, read-only:

```
status DRAFT   trust_state UNVERIFIED   contract v3   languages python
Reference #2  APPROVED  active  18ad8f39…
Oracle evidence — 12 case(s)   all ORACLE_BACKED, runs=2, stored == oracle
Quality gate: tier1 1.0  tier2 1.0  verdict PASS

No blockers. This artifact is approvable.

Artifact digest  b1df39f5d0aa73088d8750ab643f6ef2eb16a16c345b85966df2498d46d4e46c
```

Cases 3, 4, 7, 9 and 10 share output digest `5feceb66ffc8` and cases 2, 6, 11,
12 share `1bad6b8cf971` — distinct inputs with the same answer, which is what a
boundary suite looks like. The case digests are all distinct.

## K. What is not done

- **q3309 is not approved and not promoted.** The digest above is a handle for
  a human, not an approval.
- The two roles `learnlm_approve_rw` and `learnlm_promote_rw` **do not exist on
  production yet**. They were exercised as real Postgres roles locally; creating
  them on Neon is the first step of the approval phase.
- q1436 has no reference solution and therefore no oracle evidence; it cannot be
  reviewed yet.
- `question_promote` refuses `DRAFT`, and q3309 is `DRAFT`. Advancing its status
  is a separate, deliberate decision — a draft statement cannot hold a proven
  answer key.
