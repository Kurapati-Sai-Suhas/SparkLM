# P2.7 — The status lifecycle now has a graph and a writer

`Question.status` had four legal values, one CHECK constraint, one consumer,
and **no writer anywhere in the repository**. It has one now:
`question_status`, the tenth least-privilege role, and the smallest transition
graph that lets a verified question be published.

The production dry-run says **LEGAL — DRAFT → PENDING_REVIEW may be applied**.
Nothing was applied. q3309 is still DRAFT/UNVERIFIED, the bank fingerprint is
unchanged, and zero `STATUS_TRANSITION` actions exist.

---

## A. The lifecycle as it actually was

Reported in full in [P2_7_STATUS_LIFECYCLE_UNDEFINED.md](P2_7_STATUS_LIFECYCLE_UNDEFINED.md):
a vocabulary (`DRAFT · PENDING_REVIEW · PUBLISHED · BLOCKED`), one negative
constraint (`NOT (DRAFT AND ORACLE_VERIFIED)`), `is_adaptive_eligible` as the
only consumer of `PUBLISHED`, two readers of `BLOCKED`, `PENDING_REVIEW`
referenced nowhere, and **zero writers**. All 2,926 questions were DRAFT and
still are.

Status does **not** gate delivery — `servable_questions` filters on placeholder
content and empty hidden tests, never on status — so `PUBLISHED` means exactly
one thing in this codebase: with `ORACLE_VERIFIED`, submissions teach the
adaptive model.

## B. The intended transition — your decision, implemented

You chose **PENDING_REVIEW first**. The graph is:

```
DRAFT ──> PENDING_REVIEW ──> PUBLISHED
              <────────────────┘
```

`Question.STATUS_TRANSITIONS`, three edges, no others. Promotion happens at
PENDING_REVIEW, where it changes nothing observable; publication is then the
single deliberate act that turns the question on. The withdrawal edge exists so
that act is not one-way — it only ever reduces eligibility, so it needs no
evidence of its own. `BLOCKED` has no edges: you chose not to give it a writer
this phase, and nothing invented one.

**Prerequisites — my call, since you had no preference.** They are per-edge,
derived from what each edge actually enables:

| edge | requires |
|---|---|
| `DRAFT → PENDING_REVIEW` | the gates, a frozen batch with a verified pre-image, and the digest handshake. **No evidence chain** |
| `PENDING_REVIEW → PUBLISHED` | all of the above **plus** ORACLE_VERIFIED, a `QuestionApproval` stamped as promoted, a freshly rebuilt artifact with no blockers, and that artifact's digest still matching the approval |
| `PUBLISHED → PENDING_REVIEW` | the gates only |

Why nothing on the first edge: it makes nothing visible and nothing eligible.
Its only effect is to satisfy the CHECK so promotion can run — and promotion
independently re-derives the approval, the evidence, the quality verdict and
the digest. Duplicating those checks here would put them in the one place that
cannot enforce them at the moment they matter.

**One rule I invented, flagged plainly.** Publication requires
`ORACLE_VERIFIED`. The model's docstring describes `PUBLISHED + UNVERIFIED` as a
legitimate "legacy" state, and this forbids creating one. That state has no
effect in this codebase — not eligible, and delivery is not status-gated — and
the rule buys the invariant *every PUBLISHED question is oracle-verified*. It is
one condition in `_publication_blockers` if you want it back.

## C. Exact role privileges

```sql
CREATE ROLE learnlm_status_rw LOGIN PASSWORD '…'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

GRANT CONNECT ON DATABASE neondb              TO learnlm_status_rw;
GRANT USAGE   ON SCHEMA public                TO learnlm_status_rw;
GRANT SELECT  ON groups_question              TO learnlm_status_rw;
GRANT UPDATE (status) ON groups_question      TO learnlm_status_rw;
GRANT SELECT  ON groups_questionpreimage      TO learnlm_status_rw;
GRANT SELECT  ON groups_remediationbatch      TO learnlm_status_rw;
GRANT SELECT, INSERT ON groups_remediationaction TO learnlm_status_rw;
GRANT SELECT  ON groups_questionapproval      TO learnlm_status_rw;
GRANT SELECT  ON groups_referencesolution     TO learnlm_status_rw;
GRANT SELECT  ON groups_oracleexecution       TO learnlm_status_rw;
```

Shaped exactly like the four existing repair roles — SELECT on the question,
UPDATE on one column, SELECT on batch and pre-image, SELECT+INSERT on the audit
table — because a status transition *is* a remediation-family write. The last
three SELECTs exist only because the publication edge re-derives the approval
chain.

Denied, and enforced by `STATUS_TRANSITION_FORBIDDEN`: `UPDATE (trust_state)`
first of all, every other question column, INSERT/UPDATE on the approval, any
write on the reference or executions, **UPDATE/DELETE on the audit trail**, and
any write on the pre-image tables. The last two came out of the mutation sweep
— a role that can rewrite the record of what it did is not audited, and a role
that can forge a pre-image can make its own write unrecoverable while appearing
reversible.

The status role and the promotion role are disjoint and each is denied the
other's column, so no connection can both verify a question and turn it on.

**Prepared, not created.** `sql/learnlm_status_rw.sql` holds the DDL with a
generated 40-character password (never printed, gitignored); `.env` has the
matching `STATUS_*` block; `settings.py` has the tenth alias;
`docs/FEATURE_FLAGS.md` documents the five variables.

## D. The command

`question_status` — deliberately not called a promotion, because it changes
availability and never trust.

```
--batch --question --to --digest --reason --operator --alias
--apply --confirm --local --dry-run
```

Dry-run by default, matching the remediation family; `--apply --confirm` to
write. `--dry-run` **beats** `--apply`, so a command line carrying both writes
nothing. Row-locked, `transaction.atomic(using=alias)`, `update_fields=["status"]`,
post-write re-read verifying that every other captured field is untouched and
that the column actually holds the target, and an append-only
`STATUS_TRANSITION` action (migration 0047).

No new audit table: `status` is already one of the seven `CAPTURED_FIELDS`, so
the pre-image holds the previous value, `post_digest` holds the new one, and
`preimage_rollback` can already restore it.

## E. Production dry-run

```
STATUS TRANSITION  (DRY RUN)
  database neondb   role learnlm_census_ro   production True   operator Suhas

  batch           p27-pilot-1 (CAPTURED)
  question        3309 — Find the Index of the First Occurrence in a Stri
  pre-image       2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
  current digest  ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e
  projected after 1a2e3115d73700b171b3fa8b075342a5d2729f7d0a6e745f51660e05c7196580

  current status  DRAFT        proposed status PENDING_REVIEW
  legal edges     DRAFT → PENDING_REVIEW, PENDING_REVIEW → PUBLISHED,
                  PUBLISHED → PENDING_REVIEW

  fields that would change:
    groups_question.status   DRAFT → PENDING_REVIEW
  fields guaranteed unchanged:
    content, trust_state, execution_contract_version, boilerplate_code,
    hidden_wrapper_code, hidden_test_cases
    groups_questionapproval / groups_referencesolution /
    groups_oracleexecution  (no write privilege on this role)

  trust_state             UNVERIFIED (unchanged)
  adaptive eligible now   False
  adaptive eligible after False

LEGAL — DRAFT → PENDING_REVIEW may be applied.
DRY RUN — nothing was written.
```

## F. Tests

`groups/test_status_transition.py` — **64 tests, all passing**. The graph and
its illegal edges; every publication prerequisite refused individually
(unverified, no approval, unpromoted approval, drifted artifact, missing
evidence); the digest handshake; unknown question, unknown batch, missing
pre-image; only `status` changes; the action is appended and correctly classed;
the pre-image still holds the original status so rollback works; dry-run writes
nothing and beats `--apply`; both production-only gate branches; two race cases
under the lock; the grant list proved sufficient and minimal over a **real
second connection** as the real role; and the role denied eleven specific
statements it must not be able to run.

## G. Mutation

33 mutants: the graph (5), the publication chain (6), the digest handshake (1),
the write and the lock (9), the audit trail (3), the gates and grant list (8),
one planted equivalent.

First sweep left **7 real survivors**, and they were worth the run:

- `M19` — my regex `"trust_state changed"` also matched the *post-write*
  backstop, so deleting the under-lock guard changed nothing the test could
  see. The assertion now matches the under-lock wording specifically.
- `M20`, `M21` — the post-write backstops had no test at all.
- `M25`, `M26`, `M27` — every test passed `--local`, so the production branch of
  the gates was never executed by anything.
- `M32` — granting the role UPDATE on the audit trail changed nothing. That was
  a real hole in the deny-list, not just a missing test, and it is now closed.

```
32 killed / 33
real survivors: 0
  E1  EQUIVALENT: capitalisation in a success message
```

## H. Regression

```
2571 passed, 2 warnings in 263.58s
```

**Infrastructure, reported separately:** the local Postgres container was down
at the start of this phase (`PoolTimeout` on every DB test) — Docker Desktop had
stopped. Restarted, container up, unrelated to any code here.

**One process failure worth recording:** the first mutation sweep was killed
mid-run. Its `atexit` restore never fired, leaving mutant `M23` live in the
working tree, and its output was buffered and lost. I found the residue by
inspection and restored the file. The runner now writes each result to disk as
it goes and drops a marker file naming the in-flight mutant, so neither failure
can recur silently.

## I. Production safety

```
q3309   DRAFT / UNVERIFIED   adaptive_eligible False   digest ebb26e7f… ✔
approval #1 promoted_at NULL       OracleExecution 24   canonical reference #2
STATUS_TRANSITION actions 0        total remediation actions 12
status values present across the bank: ['DRAFT']
fingerprint 9cc3c8d8b0d050d0cebb6a43a44adbb68612d2e5077129c915481c1b66715acf ✔
```

This phase wrote nothing to production.

## J. What remains before promotion

1. **Create the role** (DDL connection):
   ```bash
   psql "<neondb_owner connection string>" -f backend/LearnLM/sql/learnlm_status_rw.sql
   ```
2. **Apply the transition** — re-run the dry-run through `--alias status` to
   confirm READY, then:
   ```bash
   python manage.py question_status --alias status --batch p27-pilot-1 --question 3309 --to PENDING_REVIEW --digest ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e --reason "advance out of DRAFT so promotion can run" --operator Suhas --apply --confirm
   ```
   Note the question digest changes when status does — the promotion preflight's
   expected digest becomes `1a2e3115d737…`. The **artifact** digest
   (`b1df39f5…`) does not: status is not part of it, so approval #1 stays valid.
3. **Then promote**, which is the previous phase's command, unchanged.
4. **Then publish** — `PENDING_REVIEW → PUBLISHED`, the act that makes q3309
   teach the adaptive model, gated on the full chain re-derived at that moment.
