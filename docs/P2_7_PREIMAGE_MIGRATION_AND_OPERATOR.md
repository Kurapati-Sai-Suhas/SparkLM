# P2.7 — Pre-image migration + safe operator workflow

**Status:** operator workflow built and proven locally. **Migration 0044 NOT
applied — blocked on credentials.** No production write of any kind occurred.
RESEED = NO.

---

## A. Migration 0044 safety audit

Final inspection before any application:

| check | result |
|---|---|
| operations | `CreateModel` ×3, `AddIndex` ×2, `AddConstraint` ×1 — nothing else |
| `RunSQL` | none |
| `RunPython` | none |
| `AlterField` / `RemoveField` / `DeleteModel` | none |
| `RenameField` / `RenameModel` / `AlterModelTable` | none |
| data mutation | none — no operation touches an existing table |
| defaults that fabricate rows | none. Defaults (`schema_version=1`, `state='OPEN'`, `was_adaptive_eligible=False`) apply only to rows inserted into the three NEW tables, which start empty |
| destructive operations | none |
| dependency | `('groups', '0043_glicko_snapshot')` — the current production head |

**Safety test added** — `groups/test_preimage_operator.py`, four tests that
fail if 0044 ever acquires a forbidden operation:

- `test_0044_contains_no_forbidden_operation` — AST over `migrations.X` calls,
  against `{RunSQL, RunPython, AlterField, RemoveField, DeleteModel,
  AlterModelTable, RenameField, RenameModel}`
- `test_0044_is_exactly_the_intended_additive_operations` — exact counts
- `test_0044_creates_only_the_three_pre_image_models`
- `test_0044_depends_on_the_expected_predecessor`

Both a planted `RunSQL` and a re-pointed dependency were killed by these in the
mutation sweep.

## B. Production migration result — **NOT APPLIED (blocked)**

**Step 2 could not be carried out. No authorized production admin credential
exists in this environment.**

Available credentials, and why each is unusable for this migration:

| credential | role | status |
|---|---|---|
| `POSTGRES_*` | `learnlm_census_ro` | **forbidden by your brief**, and holds no DDL privilege |
| `PILOT_*` | `learnlm_pilot_rw` | **forbidden by your brief** |
| `AZURE_POSTGRES_*` | `suhas_admin` | **placeholder** — host is `your-azure-db-host`, password empty, points at Azure not Neon; also excluded by the standing "no AZURE_POSTGRES_* fallback" rule |

There is no `neondb_owner` or other admin credential present. I did not use a
forbidden role, and I did not fall back to the placeholder.

This matches how migration 0038→0043 was handled: you applied it, I verified.
The command to run is:

```bash
cd backend/LearnLM && python manage.py migrate groups 0044
```

Run it with the admin/owner connection, targeting `neondb` only. It applies
`groups.0044` alone.

## C. Production integrity — BEFORE baseline captured

Read-only via `learnlm_census_ro`:

```
database         neondb
role             learnlm_census_ro
server           PostgreSQL 17.10 (29ad1b7)
latest migration 0043_glicko_snapshot

questions                2926
  PUBLISHED              0
  ORACLE_VERIFIED        0
  declaring v3           0
reference solutions      1
oracle executions        20
question approvals       0
code submissions         44
  adaptive_eligible      0

grading-truth fingerprint  1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f

pre-image tables present   0/3  []
```

The fingerprint is a sha256 over every question's `id`, `hidden_test_cases`,
`content`, `status`, `trust_state` and `execution_contract_version`. Counts
alone would not catch an in-place edit that preserved row counts — which is
exactly what a non-additive migration would look like. **After 0044 is applied,
this value must be byte-identical.**

The AFTER run is `scratchpad/production_integrity.py`, which additionally
reports the new tables, their indexes and the unique constraint.

## D. Pre-image schema verification

Not yet possible — the migration is unapplied and all three tables are absent
(`0/3`, confirmed above). The verification is written and ready; it checks
tables present, indexes present, the `pre_image_one_per_question_per_batch`
unique constraint, **zero pre-image rows**, and every count and the fingerprint
in §C unchanged.

## E. Operator workflow

Three commands, one shared gate module, deliberately separate:

| command | writes | default |
|---|---|---|
| `preimage_capture` | `RemediationBatch`, `QuestionPreImage` — **nothing else** | dry-run |
| `preimage_inspect` | nothing; **no `--apply`, no `--confirm`** | read-only always |
| `preimage_rollback` | `Question` (restore) + one append-only action row | dry-run |

`groups/management/commands/_preimage_ops.py` holds every gate, so a check
cannot be present in one command and forgotten in another:

1. **operator** — staff + active, via the existing `_question_trust.resolve_operator`
2. **identity** — target must be exactly `neondb`; `--local` must *not* be
3. **role** — refuses `learnlm_census_ro` and `learnlm_pilot_rw`
4. **write privilege** — role must hold `INSERT` on the pre-image table
5. **confirmation** — `--confirm` required for every production write

The two refused roles are refused for *opposite* reasons, and the message says
which: the census role **cannot** write (so failure would come late, after the
operator believes the capture began), and the pilot role **can** write but is
scoped to the reference pilot, so rows written through it would sit outside the
privilege boundary reviewed for them.

**No `--force` and no `--yes`.** The single override is
`--allow-divergence`, on `preimage_rollback` only, named for exactly what it
permits — restoring over a question edited *after* the remediation. It cannot
override a corrupt pre-image, and the override is written into the audit
record. Tests assert `--force`/`--yes` appear in no command's parser, and that
`--allow-divergence` appears on rollback alone.

**Capture and modify cannot happen in one call.** Capture writes pre-image rows
only; freeze is a *separate invocation* so the operator sees the batch before
closing it; remediation is a different workflow entirely.

## F. Capture / freeze / rollback guarantees

**Capture** — refuses unknown questions, refuses a batch with no stated
purpose, refuses to add to a frozen batch, and re-captures idempotently: the
**first** pre-image wins and is never overwritten, because it is the state
rollback must return to. Every captured row is verified immediately after
writing.

**Freeze** (Step 5) — verifies **every** pre-image before closing membership,
refuses an empty batch, and stamps `frozen_at`/`frozen_by`, moving the batch to
`CAPTURED`. Afterwards: membership cannot change, duplicates are impossible
(`UniqueConstraint(batch, question)`), and a member cannot silently disappear
(`on_delete=PROTECT` throughout). The batch carries id, purpose, operator,
`created_at`, `frozen_at`, schema version, and a per-member `state_digest`.

**Rollback** — verifies everything *before* writing anything, then applies
inside one transaction with a post-restore digest check; any failure reverts
the whole batch. Divergence is detected and refused rather than overwritten.

## G. Authorization and write boundary

`ROLLBACK_SCOPE = ("groups_question",)` — unchanged and asserted by test.
Rollback restores question state only. A structural test asserts the rollback
command references none of `ReferenceSolution`, `OracleExecution`,
`QuestionApproval`, `CodeSubmission`, `RecommendationLog`. Historical events
stay historical: an oracle run happened and a person approved, and undoing the
data does not un-happen either. Rollback **appends** one action row and deletes
nothing.

Authorization reuses the repository's existing convention exactly — no second
scheme was introduced.

## H. Mutation results

**18 killed / 18. Zero unexplained survivors.** One planted equivalent (a label
width in a printed header).

Killed: role gate disabled; census role removed from the refusal list; pilot
role removed; production-identity check disabled; production target blanked;
`--local` running against production; confirmation skipped; INSERT-privilege
check skipped; capture dry-run writing; rollback dry-run restoring; capture
writing questions; capture freezing implicitly; frozen batch accepting members;
purposeless batch; missing questions ignored; `RunSQL` added to 0044;
dependency re-pointed.

**Three real survivors were found and closed**, and one is worth recording:

> **M12 — capture also writes `Question` rows — survived.** My structural guard
> looked for `Question.objects.<verb>` and `Question.save`, but the mutant used
> `for q in questions: q.save(...)`, writing through a loop variable that
> matches no such pattern. Enumerating forbidden *receivers* is not checkable;
> naming permitted *operations* is. The guard now asserts `preimage_capture`
> calls `.save()`, `.update()` or `.delete()` on **nothing at all**.

The other two were gates that existed as functions but were only tested
directly, leaving `run_gates` free to stop calling them. They are now driven
through the real entry point.

**Full regression: 2,080 passed, 0 failed, 0 errors.**

## I. Production pre-images captured? **NO.**

Zero. The pre-image tables do not exist on production yet.

## J. Grading truth changed? **NO.**

Fingerprint `1981a621…` recorded above; production remains at migration
`0043_glicko_snapshot`; the only role used this phase was `learnlm_census_ro`,
which holds no write privilege.

## K. Exact next prerequisite before the 7-question pilot

1. **Apply migration 0044** with the admin/owner connection — the one step
   blocked here. `python manage.py migrate groups 0044`.
2. **Provide the authorized role** for capture, or confirm you will run the
   capture yourself. The operator commands refuse `learnlm_census_ro` and
   `learnlm_pilot_rw`, so a third role — or the owner — must perform it.
3. **Re-run `production_integrity.py`** and confirm the fingerprint and every
   count are unchanged, tables/indexes/constraint present, zero pre-image rows.
4. **Then, and only then**, dry-run the pilot capture:
   `preimage_capture --batch <key> --questions 1689 963 3309 17 266 1436 264
   --purpose "..." --operator <you>` — and stop for review before `--apply`.

---

## Final status

```
PRE-IMAGE/ROLLBACK TOOLING = PROVEN LOCALLY
MIGRATION 0044             = NOT APPLIED (blocked: no authorized admin credential)
PRODUCTION PRE-IMAGES      = NONE
PRODUCTION REMEDIATION     = NOT STARTED
RESEED                     = NO
```

I could not reach `PRE-IMAGE/ROLLBACK = PRODUCTION-READY` because that requires
the schema to exist on production, and applying it requires a credential this
environment does not hold. Everything that did not depend on it is complete.
