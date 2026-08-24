# P2.7h-16 — Reseed migration applied to production and verified

Supersedes the blocker recorded in `P2_7_RESEED_MIGRATION_BLOCKED.md`. The owner
applied 0047 and 0048 on 2026-08-23 at 14:28 UTC. Everything below is
**read-only verification**: no role was created, no privilege granted, no row
written, no ledger row created, no batch created, no reseed started.

---

## 1. Migration state — VERIFIED

```
groups.0046_suite_expansion_action_class      previously applied
groups.0047_status_transition_action_class    applied  2026-08-23 14:28:45
groups.0048_reseed_action_classes_and_ledger  applied  2026-08-23 14:28:48
```

Graph tail is exactly `0046 → 0047 → 0048`, and 0048 is the leaf of the app.
Querying `django_migrations` for everything applied after 0046 returns
**exactly two rows, both in `groups`** — no other app moved, nothing unexpected
rode along.

The two `AlterField` operations were no-ops as predicted:
`groups_remediationaction.action_class` is still `character varying(32) NOT NULL`,
the table still carries **0 CHECK constraints**, and still has 10 columns.
Django `choices` remain un-enforced by the database, exactly as before.

---

## 2. Ledger schema — VERIFIED

Columns match the migration exactly, in order:

```
id           bigint                     NOT NULL   identity
stage        character varying(24)      NOT NULL
last_error   text                       NOT NULL
attempts     integer                    NOT NULL
created_at   timestamp with time zone   NOT NULL
updated_at   timestamp with time zone   NOT NULL
batch_id     bigint                     NOT NULL
question_id  bigint                     NOT NULL
```

Constraints and indexes as designed:

```
[c] groups_reseedledger_attempts_check          CHECK ((attempts >= 0))
[p] groups_reseedledger_pkey                    PRIMARY KEY (id)
[u] reseed_ledger_one_per_question_per_batch    UNIQUE (batch_id, question_id)
[f] → groups_remediationbatch(id)               DEFERRABLE INITIALLY DEFERRED
[f] → groups_question(id)                       DEFERRABLE INITIALLY DEFERRED

idx reseed_ledger_batch_stage_idx   btree (batch_id, stage)
idx groups_reseedledger_batch_id_37096568
idx groups_reseedledger_question_id_e4f65b45
```

Both foreign keys report `confdeltype = 'a'` (NO ACTION) — the intended
behaviour. The database guarantees a ledger row can never be orphaned;
`on_delete=PROTECT` turns that into a clean application-level refusal rather
than a cascade. **Nothing cascades from the ledger.**

Stage domain is the five designed values — `PENDING`, `STATEMENT_WRITTEN`,
`SIGNATURE_WRITTEN`, `COMPLETE`, `FAILED` — and the longest fits `varchar(24)`.

**Absences confirmed.** Neither the table nor the model declares any of
`digest`/`state_digest`, `status`, `trust_state`, `hidden_test_cases`,
`expected_output`, `approval`, `published`/`publication`, or
`adaptive_eligible`. Model fields are exactly:

```
attempts, batch, created_at, id, last_error, question, stage, updated_at
```

Schema footprint moved by precisely the new table: **58 → 59 tables**,
**430 → 438 columns**.

---

## 3. Permissions — VERIFIED, with one finding

Owner is `neondb_owner`. **Direct grants on `groups_reseedledger`:**

```
learnlm_census_ro    SELECT      (and nothing else)
```

The predicted automatic grant occurred: production's
`ALTER DEFAULT PRIVILEGES neondb_owner → tables → learnlm_census_ro = SELECT`
fired on table creation. No `INSERT`, `UPDATE` or `DELETE` was granted to
anyone.

**Effective privilege by role:**

```
role                     SELECT  INSERT  UPDATE  DELETE
learnlm_census_ro         yes      no      no      no
learnlm_pilot_rw          yes      no      no      no      ← finding
learnlm_approve_rw         no      no      no      no
learnlm_boilerplate_rw     no      no      no      no
learnlm_contract_rw        no      no      no      no
learnlm_hidden_test_rw     no      no      no      no
learnlm_oracle_rw          no      no      no      no
learnlm_preimage_rw        no      no      no      no
learnlm_promote_rw         no      no      no      no
learnlm_remediate_rw       no      no      no      no
learnlm_status_rw          no      no      no      no
```

**No `learnlm_*` role can write the ledger.** That is the safety-critical
claim and it holds — the table is unwritable by every application role until a
future phase deliberately grants it.

### Finding: `learnlm_pilot_rw` reads the ledger by inheritance

I predicted `learnlm_census_ro` would be the only reader. It is not.
`learnlm_pilot_rw` also has SELECT — but holds **zero direct grants** on the
table, or on any table:

```
learnlm_pilot_rw   MEMBER OF   learnlm_census_ro   (rolinherit = true)
```

So it inherits every read privilege census holds, across the whole database —
`groups_question`, `groups_codesubmission`, `groups_questionapproval` and now
the ledger — and it acquires read access to any future table census is granted,
automatically.

This is a **pre-existing property of the pilot role**, not an effect of this
migration. The migration granted census SELECT; membership did the rest. The
role is otherwise dormant: it can log in, its credentials sit in `.env`, no
`settings.py` alias uses it, and it has no write privilege anywhere.

Reported, not altered — the brief forbids changing grants, and read access is
not authority in this architecture. But "pilot inherits census" is worth an
explicit decision before the pilot phase, because a role named for a write
workflow currently carries bank-wide read by inheritance and nothing else.

---

## 4 & 6. Production immutability — VERIFIED

Applying the migration changed no application data. Every bank-wide invariant
holds at its expected value:

```
total questions                 2926  ✔        approvals               2  ✔
reseed candidates               1141  ✔        executions             70  ✔
servable                        1784  ✔        remediation actions    17  ✔
published                          2  ✔        pre-images              7  ✔
oracle_verified                    2  ✔        references              3
submissions                       44  ✔
adaptive_eligible submissions      0  ✔

remediation action rows sha256
  bf38c4bbadab1c815c12f1eb062234c0bbdf0daea3bc9cda9b09b1201c108a09  ✔ unchanged

bank fingerprint
  3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  ✔ unchanged
```

The fingerprint covers `hidden_test_cases`, `content`, `status`, `trust_state`
and `execution_contract_version` for all 2,926 rows, so its stability is a
single statement covering question content, status, trust state and hidden
tests together. References, executions, approvals, submissions, remediation
actions and pre-images are each unchanged by count, and the action rows are
unchanged by content hash.

---

## 5. Named questions — VERIFIED

```
q3309  PUBLISHED / ORACLE_VERIFIED  12 cases  98ebfdfecc6b47963947577fa469cfd2a77aec31d79a45f61b4e90100cb873f2  ✔
q1436  PUBLISHED / ORACLE_VERIFIED  13 cases  6bb2f3538e093d027c97b6e032787c5d39322789516b3dfa9720744b41f84ab8  ✔
q2201  DRAFT     / UNVERIFIED        4 cases  647d62d778420c06ceef9bc2634a708222489319fe350e15223cd1be782634dc  ✔
```

All three digests identical to the values recorded before the apply.

---

## 7. Ledger empty, reseed not started — VERIFIED

```
select count(*) from groups_reseedledger   →  0     (raw SQL)
ReseedLedger.objects.count()               →  0     (ORM)
questions with a ledger row                →  0
batches                                    →  [('p27-pilot-1', 'CAPTURED')]   pre-existing
STATEMENT_GENERATION actions               →  0
SIGNATURE_DECLARATION actions              →  0
```

The only batch is `p27-pilot-1`, which predates this milestone and belongs to
the pre-image work. No reseed batch exists.

Action classes in use are unchanged from before the migration:

```
STATUS_TRANSITION 4 · STATEMENT_REPAIR 3 · SUITE_EXPANSION 2 · INPUT_REPAIR 2
CONTRACT_REPAIR 2 · HIDDEN_TEST_REPAIR 2 · ROLLBACK 1 · BOILERPLATE_REPAIR 1
```

Both new classes are selectable and entirely unused. No code writes the ledger,
and no management command references either new class — confirmed by search.

---

## 8. Regression

```
2724 passed, 2 warnings in 249.84s
migrate --check (production):  exit 0 — nothing pending, in any app
showmigrations:                0 unapplied across all apps
```

Run against the local test database (`pytest --ignore=scripts`, see
`P2_7_RESEED_MIGRATION_BLOCKED.md` §10d). No production mutation.

---

## 9. Discrepancies

One, reported and not fixed: **§3, `learnlm_pilot_rw` inherits SELECT on the
ledger** through membership of `learnlm_census_ro`. Everything else matched
prediction exactly.
