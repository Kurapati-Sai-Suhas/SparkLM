# P2.7 — q3309 promotion: ready, not applied

The preflight recomputes the approved digest **exactly** and reports two things
standing between q3309 and `ORACLE_VERIFIED`: the promotion role, which is
prepared but not yet created, and **q3309 being DRAFT**, which the brief said to
surface rather than fix.

Nothing was promoted. `trust_state` is `UNVERIFIED`, approval #1 is unstamped,
and the bank fingerprint is unchanged.

Reading the existing contract against what the command actually does turned up
**two defects that would have made promotion impossible or unsafe**, both fixed
here.

---

## A. The existing promotion lifecycle, as it stands

| question | answer |
|---|---|
| CLI | `--question --operator --confirm --alias`, now also `--dry-run`. Deliberately no `--force`, no `--trust-state`, no `--skip-*` |
| status requirement | refuses `DRAFT`; does **not** advance it. Also a DB CHECK, `question_draft_cannot_be_oracle_verified` |
| transition | `trust_state: UNVERIFIED → ORACLE_VERIFIED`, one column |
| fields written | `groups_question.trust_state`; `groups_questionapproval.promoted_at`, `.promoted_by_id`. Nothing else |
| row locking | **was absent** — added this phase |
| digest | rebuilt from live state (`trust.build`) and compared to `approval.artifact_digest` |
| approval checks | exists; artifact schema version matches; reference id matches; reference source hash matches; frozen quality verdict passed |
| oracle evidence | re-collected, not read back: ≥2 agreeing SUCCESS runs per case, scoped to the reference's current source hash, no nondeterministic row, no conflict |
| quality gate | the verdict **frozen on the approval**, not re-run — re-running needs Judge0, and making trust depend on a reachable external service invites a `--skip` flag |
| reference | `canonical_reference` re-derived live: approved, active, source unmodified since approval |
| adaptive eligibility | **not recalculated by promotion.** `is_adaptive_eligible` is a property requiring `PUBLISHED and ORACLE_VERIFIED`, read once and frozen onto each submission |
| alias / role | `--alias promote` → `learnlm_promote_rw` |

**The status question, explicitly.** Promotion does not require a status
transition of its own — it requires the question to already not be DRAFT. So
q3309 needs its status advanced by something else before promotion, and **no
command in this repository writes `Question.status`**. That writer does not
exist yet; it is the next thing this phase needs and it is not in this brief's
scope, so I have not invented one.

## B. Two defects in the contract as written

**1. The promotion contract could not be satisfied by any role.**
`PROMOTION_FORBIDDEN` listed table-level `UPDATE` on `groups_questionapproval`
as excess privilege — but the command writes `promoted_at` and `promoted_by` on
that very table. Grant it and the forbidden check refuses; withhold it and the
write fails mid-transaction. The probe never named those columns at all.

**2. The probe demanded a privilege nothing uses.** It required
`UPDATE (status)` on `groups_question`, which the command never writes. A role
built to satisfy it could have published a question as a side effect of being
allowed to promote one.

Both fixed:

```python
PROMOTION_PROBE = (
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_questionapproval", "promoted_at", "UPDATE"),
    ("groups_questionapproval", "promoted_by_id", "UPDATE"),
)
```

`PROMOTION_FORBIDDEN` is now column-scoped throughout and **complete**: every
column of `groups_question` except `trust_state`, and every column of
`groups_questionapproval` except the two stamp columns, is named — `status`
included. A table-level check cannot tell "may stamp the promotion" from "may
rewrite the approval", which is exactly the trap the old list fell into.

**3. Row locking was absent.** Every check ran against a question read without a
lock, and the write happened later. Added: inside the transaction, the question
and the approval are re-read `FOR UPDATE`, and then

- the approval must still be unstamped,
- the question must still be `UNVERIFIED` and not `DRAFT`,
- the artifact is **rebuilt under the lock** and compared to the digest on the
  approval — not to the value computed before the lock, which would prove
  nothing.

## C. Exact promotion role privileges

```sql
CREATE ROLE learnlm_promote_rw LOGIN PASSWORD '…'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

GRANT CONNECT ON DATABASE neondb                 TO learnlm_promote_rw;
GRANT USAGE   ON SCHEMA public                   TO learnlm_promote_rw;
GRANT SELECT  ON groups_question                 TO learnlm_promote_rw;
GRANT UPDATE (trust_state) ON groups_question    TO learnlm_promote_rw;
GRANT SELECT  ON groups_referencesolution        TO learnlm_promote_rw;
GRANT SELECT  ON groups_oracleexecution          TO learnlm_promote_rw;
GRANT SELECT  ON groups_questionapproval         TO learnlm_promote_rw;
GRANT UPDATE (promoted_at, promoted_by_id)
      ON groups_questionapproval                 TO learnlm_promote_rw;
```

It cannot: INSERT or DELETE an approval; change any approval column other than
the two stamp columns (not the digest, not the quality verdict, not who
approved it); write any question column other than `trust_state` — `status`
included; write a reference or an execution; read any column of `groups_user`.

Two grants I drafted and then removed, because the minimality test proved
neither was used:

- `SELECT (id) ON groups_user` — the approval path needs it because
  `full_clean` validates FKs on a new row; promotion writes named columns on
  rows that already exist, so no FK is ever validated.
- The `groups_questionapproval_id_seq` grant (removed in the approval phase for
  the same reason: the pk is `GENERATED BY DEFAULT AS IDENTITY`).

**Status: prepared, not created.** `learnlm_promote_rw` does not exist on
production; I have no DDL-capable connection.
`backend/LearnLM/sql/learnlm_promote_rw.sql` holds the DDL with a generated
40-character password (never printed, gitignored), `.env` has the matching
`PROMOTE_*` block, `settings.py` has the ninth alias, and
`docs/FEATURE_FLAGS.md` documents the five variables.

`SELECT … FOR UPDATE` on a column-level `UPDATE` grant was the one thing I could
not settle by reading the docs. The end-to-end test answers it: PostgreSQL
accepts it.

## D. Preflight output (real, production, read-only)

```
The approval being acted on
  approval            #1
  approved by / at    user 1 / 2026-08-19 17:07:13 UTC
  approved digest     b1df39f5d0aa73088d8750ab643f6ef2eb16a16c345b85966df2498d46d4e46c
  recomputed digest   b1df39f5d0aa73088d8750ab643f6ef2eb16a16c345b85966df2498d46d4e46c
  match               True
  artifact schema     v1 (code computes v1)
  already promoted    no

Re-derived from live state
  question state      ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e
  status / trust      DRAFT / UNVERIFIED
  execution contract  v3
  canonical reference #2 APPROVED active=True
  reference hash      18ad8f390642c315ef78e52fecaf97ab31fd6fc69fca2a02b3114e03d77a341b
  == approved hash    True
  cases 12   oracle-backed 12/12   agreeing runs [2]
  quality (frozen)    tier1 1.0 / tier2 1.0 — PASS

Fields promotion would write
  groups_question.trust_state          UNVERIFIED → ORACLE_VERIFIED
  groups_questionapproval.promoted_at  NULL → now()
  groups_questionapproval.promoted_by  NULL → 1

Fields promotion would NOT write
  groups_question.status               stays DRAFT — promotion does not publish
  .title .content .base_difficulty .topic_id .hidden_test_cases
  .boilerplate_code .hidden_wrapper_code .execution_contract_version
  groups_referencesolution             (no write privilege)
  groups_oracleexecution               (no write privilege)

Adaptive eligibility
  now False   after promotion False  (needs PUBLISHED as well)

NOT PROMOTABLE — 2 reason(s):
  • connected as 'learnlm_census_ro' … Reconnect as learnlm_promote_rw.
  • question 3309 is DRAFT. A draft cannot hold a proven answer key.
```

Every evidence check passes. The two blockers are the role and the status.

## E. Tests

`groups/test_promotion_write_path.py` — **48 tests, all passing**, new this
phase. Covering the brief's twenty items: approved digest required; stale digest,
changed contract, changed reference hash, retired reference, missing approval,
failing frozen quality, missing evidence, single agreeing run, conflicting
evidence, DRAFT, unknown question, missing `--confirm` — each refused; the exact
transition (only `trust_state` moves, `status` does not); adaptive eligibility
in both directions; a second promotion as a safe no-op; the two race cases under
the lock; alias routing by AST; the grant list proved sufficient and minimal over
a real second connection; the promoter denied eleven specific statements it must
not be able to run; production identity gate; and a repo-wide AST sweep asserting
`question_promote` is **the only module in the codebase that assigns
`trust_state`**.

## F. Mutation

36 mutants: the checks promotion rests on (10), the lock and transaction (7),
what is written (5), routing (3), gates and grant list (8), the adaptive
invariant (1), the preflight (1), one planted equivalent.

First sweep left two survivors — `M15` and `M16`, the under-lock guards, which
no test reached because the pre-lock "already promoted, nothing to do" branch
fires first in an uncontended run. Two tests now create the actual race by
changing the row after the pre-lock checks and before the lock.

```
35 killed / 36
real survivors: 0
  E1  EQUIVALENT: wording in a refusal message
```

Worth naming: `M29` (grant the promoter `UPDATE (trust_state, status)`) and
`M28` (grant it `INSERT` on the approval) are both killed by the minimality and
separation tests, and `M33` (reinstate the self-contradicting table-level denial)
is killed by the contract-consistency test written for defect 1.

## G. Regression

```
2507 passed, 2 warnings in 213.49s
```

No infrastructure failures this phase.

## H. Production safety

```
q3309   DRAFT / UNVERIFIED   adaptive_eligible False
        digest ebb26e7f… ✔   OracleExecution 24   QuestionApproval 1
approval #1  promoted_at NULL  promoted_by NULL
ORACLE_VERIFIED anywhere in the bank: 0
q1436 / q963 / q17 unchanged;  q264 / q266 / q1689 at pre-image
fingerprint 9cc3c8d8b0d050d0cebb6a43a44adbb68612d2e5077129c915481c1b66715acf ✔
```

This phase wrote nothing to production.

## I. The promotion command for the next phase

Two things must happen first, in this order.

**1. Create the role** (DDL connection):

```bash
psql "<neondb_owner connection string>" -f backend/LearnLM/sql/learnlm_promote_rw.sql
```

**2. Advance q3309 out of DRAFT.** No command writes `Question.status` today,
and no role holds `UPDATE (status)` — the promotion role is explicitly denied
it. That writer needs designing, reviewing and its own column-scoped role, the
same way every other write in this milestone did. It is the gating decision for
the promotion phase, not a detail of it.

Then, from `backend/LearnLM`, preflight first:

```bash
python manage.py question_promote --question 3309 --operator Suhas --alias promote --dry-run
```

It should print **PROMOTABLE**. Only then:

```bash
python manage.py question_promote --question 3309 --operator Suhas --alias promote --confirm
```

Promotion still does not publish. Until q3309 is `PUBLISHED` as well,
`is_adaptive_eligible` stays false and no submission teaches the model.
