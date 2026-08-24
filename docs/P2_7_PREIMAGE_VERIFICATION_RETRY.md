# P2.7 — Pre-image verification retry: target mismatch

**0044 is still not visible on the database this repo connects to.** Production
grading truth is unchanged. Nothing was captured.

Since the migration has now been applied twice and is absent both times, the
likely explanation is no longer "it failed" but **"we are connected to
different targets."** This report pins down exactly what mine is so you can
compare it with yours.

---

## What this connection reaches

```
configured      NAME=neondb  USER=learnlm_census_ro  PORT=5432
                HOST=ep-blue-hat-aj7p2x8v-pooler.<redacted>

server says     current_database  neondb
                current_user      learnlm_census_ro
                session_user      learnlm_census_ro
                current_schema    public
                search_path       "$user", public
                server_version    17.10 (29ad1b7)
```

Ruling out the three ways this could be a false alarm:

| hypothesis | check | result |
|---|---|---|
| tables exist but census role lacks GRANT | `pg_class`, **not** privilege-filtered | **NONE in any schema** |
| tables exist in a different schema | every schema enumerated | only `information_schema`, `public` |
| migration recorded but tables missing | `django_migrations` tail | `groups.0043_glicko_snapshot`, 2026-08-13 09:42:42 |

`0044` appears in `django_migrations` **zero** times. 74 migration rows total.

## Comparison markers

Run these on **the same connection you used for the migrate**, and compare:

```sql
select current_database(), current_user, inet_server_addr();
select count(*) from pg_class where relname = 'groups_questionpreimage';
select count(*) from django_migrations where app='groups' and name like '0044%';
select max(id), count(*) from groups_question;
select md5(string_agg(name, ',' order by id)) from django_migrations where app='groups';
```

What this connection returns:

| marker | value here |
|---|---|
| `pg_class` rows for `groups_questionpreimage` | **0** |
| `django_migrations` rows matching `0044%` | **0** |
| `max(groups_question.id)` | **3416** |
| `count(groups_question)` | **2926** |
| groups migration-list md5 | `5cf2dd8625f01c658f316c6d899b5bf2` |
| endpoint | `ep-blue-hat-aj7p2x8v-pooler` |

If your connection returns `1` for the first two, the migration is applied —
just not to this endpoint/database. Then `max(groups_question.id)` and the
endpoint id identify which is which. Because Neon branches are copy-on-write
forks, a branch will hold the same 2,926 questions, so **question counts alone
cannot tell two branches apart** — the endpoint id and the migration md5 can.

## One more candidate worth ruling out

This endpoint hosts four databases:

```
neondb   <- connected
postgres
template1
test_neondb
```

`test_neondb` is a leftover from the earlier incident where pytest nearly built
its test database on production (the read-only role stopped it). If the owner
connection string ended in `/test_neondb` or `/postgres`, the migration would
have applied cleanly to the wrong database. Worth checking, and `test_neondb`
is worth dropping once this is settled.

---

## Report

| | |
|---|---|
| **A. 0044 applied?** | **NO** on `neondb` at `ep-blue-hat-aj7p2x8v` |
| **B. Schema verified?** | **NO** — no relations to inspect |
| **C. Counts unchanged?** | **YES** — 2926 / 0 / 0 / 0 / 1 / 20 / 0 / 44 / 0, all exact |
| **D. Fingerprint unchanged?** | **YES** — `1981a621…46f49f061533d31b8246f`, byte-identical |
| **E. Tables present and empty?** | **Absent**, so vacuously empty |
| **F. Census can read all three?** | **N/A** — the grant may well have succeeded on the target you migrated; it cannot be confirmed against tables that do not exist here |
| **G. Operator gates verified?** | **YES** — 91 tests, no production write |
| **H. Dry-run successful?** | **NO** — `ProgrammingError: relation "groups_remediationbatch" does not exist`, raised while *reading*. Zero writes |
| **I. Intended membership** | 1689, 963, 3309, 17, 266, 1436, 264 — could not be resolved against production |
| **J. Capture blockers** | One: the schema is absent here. No per-question blocker is expected — capture stores broken suites verbatim and there are tests for exactly that |
| **K. Next action** | Compare the markers above from your migrate connection and identify which target received 0044 |

## Final status

```
0044                     = NOT VISIBLE ON neondb @ ep-blue-hat-aj7p2x8v
PRE-IMAGE FOUNDATION     = NOT VERIFIED IN PRODUCTION
PRE-IMAGE TOOLING        = PROVEN LOCALLY (91 tests)
PRODUCTION GRADING TRUTH = UNCHANGED (fingerprint identical)
PRE-IMAGE CAPTURE        = NOT APPLIED
REMEDIATION              = NOT STARTED
RESEED                   = NO
TRANSFORMER KT           = NOT STARTED
```

I have not reported `VERIFIED IN PRODUCTION`, because the schema is not present
on the database this repository connects to. If it is present on yours, the
foundation may well be fine and the gap is which connection the application
uses — which is worth resolving before any capture, since the capture must land
on the same database the app grades from.
