# P2.7 — Production pre-image verification after 0044

**Result: migration 0044 is NOT present on `neondb`.** Production grading truth
is intact and unchanged. Nothing was captured, nothing was remediated.

I could not report the requested final status, because the schema the phase
depends on does not exist on the database. Everything that did not depend on it
was verified.

---

## A. 0044 applied? **NO**

Three independent checks, all through `learnlm_census_ro`:

```
django_migrations, app='groups', latest 6:
  0043_glicko_snapshot                 2026-08-13 09:42:42
  0042_question_approval               2026-08-13 09:42:39
  0041_output_provenance               2026-08-13 09:42:34
  0040_shadow_adaptive_model           2026-08-13 09:42:30
  0039_reference_solution_lifecycle    2026-08-13 09:42:27
  0038_codesubmission_adaptive_...     2026-08-13 09:42:23

rows matching '0044%'          : 0
pg_class relations found       : NONE
information_schema visible     : NONE
```

**This is not a privilege artifact, and I checked specifically for that.**
`information_schema.tables` is filtered by grants, so "0 tables" there could
have meant "created but not granted". `pg_class` is **not** filtered that way
and shows the three relations do not exist at all. The sanity check in the same
query confirms the role can see `groups_question`, so the queries themselves
work.

The local Docker dev database is at `0037`, so the migration did not land there
by accident either.

**Most likely cause:** `manage.py migrate` reads `POSTGRES_*` from `.env`,
which is `learnlm_census_ro` — a role with no DDL privilege. If the migrate was
run from the repo without repointing that connection at the owner, it would
have failed rather than applied, and the failure is easy to miss in Django's
output.

## B. Production schema verified? **NO — nothing to verify**

Tables, indexes, unique constraint, foreign keys and column set could not be
inspected because the relations are absent. The verification script is written
and ready (`scratchpad/production_integrity.py` plus the catalogue queries) and
will run as-is once the migration lands.

## C. Integrity counts unchanged? **YES — every one**

| metric | baseline | now |
|---|---:|---:|
| questions | 2926 | **2926** |
| PUBLISHED | 0 | **0** |
| ORACLE_VERIFIED | 0 | **0** |
| declaring v3 | 0 | **0** |
| ReferenceSolution | 1 | **1** |
| OracleExecution | 20 | **20** |
| QuestionApproval | 0 | **0** |
| CodeSubmission | 44 | **44** |
| adaptive_eligible | 0 | **0** |

Identity confirmed: `database=neondb`, `role=learnlm_census_ro`,
`PostgreSQL 17.10 (29ad1b7)`. The owner role was not used for any verification
query.

## D. Fingerprint unchanged? **YES**

```
1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
```

Byte-identical to the recorded baseline. Whatever happened during the migration
attempt, **it did not touch grading truth** — which is the reassuring half of
this report.

## E. Pre-image tables empty? **They do not exist**

Vacuously zero rows, but the honest answer is absence, not emptiness. No
production pre-image exists.

## F. Operator boundary verified? **YES — by test, with no production write**

91 tests pass (`test_preimage_operator.py` + `test_pre_image.py`):

| requirement | verified |
|---|---|
| `learnlm_census_ro` rejected for writes | yes |
| `learnlm_pilot_rw` rejected for writes | yes |
| an owner/admin role accepted | yes |
| production identity mandatory | yes (wrong DB refused; `--local` refuses production) |
| `--confirm` required for writes | yes, through `run_gates` |
| no `--force`, no `--yes` | yes, by AST over the parsers |
| `--allow-divergence` on rollback only | yes |
| capture cannot modify `Question` | yes — capture calls `.save()`/`.update()`/`.delete()` on nothing |
| rollback cannot modify ReferenceSolution / OracleExecution / QuestionApproval / CodeSubmission / RecommendationLog | yes, structurally |

No production write was performed to demonstrate any of this, per your
instruction.

## G. 7-question dry-run successful? **NO — blocked by the missing schema**

Attempted exactly as specified, dry-run only, no `--apply`:

```
python manage.py preimage_capture --batch p27-pilot-1 \
  --questions 1689 963 3309 17 266 1436 264 \
  --purpose "remediation pilot" --operator Suhas
```

```
django.db.utils.ProgrammingError: relation "groups_remediationbatch" does not exist
```

The command failed while *reading* whether the batch already exists — before
any planning output. **No rows were created**; a dry-run has no write path at
all.

## H. Intended batch membership

Seven questions, unchanged from the design:

| id | role in the pilot |
|---|---|
| 1689 | semantic answer-key conflict (all four keys wrong) |
| 963 | statement/example defect + INVALID_INPUT |
| 3309 | CONTRACT_MISMATCH |
| 17 | INVALID_INPUT (list-valued `expected_output`) |
| 266 | NEEDS_MIGRATION (boolean casing) |
| 1436 | semantic conflict + unannotated parameter |
| **264** | **SAFE control — must remain byte-identical** |

Membership could not be *resolved against production* in this run, so the count
of 7 is the intent, not a verified fact.

## I. Any question that cannot be captured safely?

**Undetermined — this needs the dry-run.** On the evidence already gathered,
none is expected to be uncapturable: capture stores whatever is there
verbatim, and a structurally broken suite is explicitly capturable (q17 and
q963 both carry list-valued `expected_output`, and there is a test for exactly
that case). The dry-run will confirm per question.

## J. Exact next action before the first production pre-image write

**1. Apply the migration with a DDL-capable connection.** The repo's `.env`
points `POSTGRES_*` at `learnlm_census_ro`, so `manage.py migrate` from the
repo will not work unless that connection is temporarily repointed at the
owner. Then:

```bash
cd backend/LearnLM && python manage.py migrate groups 0044
```

Check the output says `Applying groups.0044_pre_image_rollback... OK`.

**2. Grant on the new tables — this will otherwise bite twice.** This is the
same trap hit after 0043 ("missing GRANT on new tables"). Both roles need it,
for different reasons:

```sql
GRANT SELECT ON groups_remediationbatch, groups_questionpreimage,
                groups_remediationaction TO learnlm_census_ro;
```

`preimage_inspect` and every dry-run read these tables, and the census role is
what those run as.

**3. Decide which role performs the capture.** The commands refuse
`learnlm_census_ro` (cannot write) and `learnlm_pilot_rw` (wrong scope), so the
capture needs the owner or a new purpose-made role with `INSERT` on the three
tables. If you create one, tell me its name and I will add it to the accepted
set — it is currently a deny-list, so any third role already passes the role
gate.

**4. Then re-run this verification**, and only after it is clean, the dry-run.

---

## Final status

```
MIGRATION 0044             = NOT PRESENT ON neondb
PRE-IMAGE FOUNDATION       = NOT VERIFIED IN PRODUCTION (schema absent)
PRE-IMAGE TOOLING          = PROVEN LOCALLY (91 tests)
PRODUCTION GRADING TRUTH   = UNCHANGED (fingerprint identical)
PRE-IMAGE CAPTURE          = NOT APPLIED
REMEDIATION                = NOT STARTED
RESEED                     = NO
TRANSFORMER KT             = NOT STARTED
```
