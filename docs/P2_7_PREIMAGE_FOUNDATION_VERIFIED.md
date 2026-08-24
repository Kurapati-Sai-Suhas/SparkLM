# P2.7 — Pre-image foundation verified in production

**0044 is present on the canonical production database and every check passes.**
Grading truth unchanged. Zero pre-images captured. RESEED = NO.

The earlier target mismatch is closed: the migration is now visible from the
same endpoint, database and role that previously reported it absent — so it
was a genuine gap, not a connection difference, and it is now resolved.

---

## A. Is 0044 genuinely present on the canonical production database? **YES**

Verified from the exact connection that reported it missing:

```
endpoint          ep-blue-hat-aj7p2x8v-pooler
current_database  neondb
current_user      learnlm_census_ro
session_user      learnlm_census_ro
server_version    17.10 (29ad1b7)
```

```
0044_pre_image_rollback   applied 2026-08-16 18:52:19
0043_glicko_snapshot      applied 2026-08-13 09:42:42

django_migrations rows matching 0044   1
```

Confirmed by **both** required routes — `django_migrations` *and* the
catalogue — not by `information_schema` alone.

## B. All three pre-image tables present? **YES**

`pg_class`: **3/3** — `groups_questionpreimage`, `groups_remediationaction`,
`groups_remediationbatch`.

**Indexes — 19 total, all four named ones present:**
`pre_image_question_ts_idx`, `pre_image_digest_idx`,
`action_batch_question_idx`, `action_question_ts_idx` (plus primary keys, FK
indexes, and the `batch_key` unique/`_like` pair).

**Constraints — 15, via `pg_constraint`:**

| kind | count | notable |
|---|---:|---|
| foreign key | 9 | every FK `PROTECT`-backed, across all three tables |
| primary key | 3 | one per table |
| unique | 2 | **`pre_image_one_per_question_per_batch`**, `groups_remediationbatch_batch_key_key` |
| check | 1 | `schema_version` non-negative (from `PositiveSmallIntegerField`) |

**Columns:** batch 8, pre-image 17, action 10 — matching the models. No
unexpected tables or constraints were created.

## C. Census role correctly read-only? **YES**

| table | privileges |
|---|---|
| `groups_remediationbatch` | `['SELECT']` |
| `groups_questionpreimage` | `['SELECT']` |
| `groups_remediationaction` | `['SELECT']` |

`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` — **none granted on any of the three.**
The grant is exactly what was needed and nothing more.

## D. Integrity counts unchanged? **YES — all nine exact**

| metric | baseline | now |
|---|---:|---:|
| questions | 2926 | 2926 |
| PUBLISHED | 0 | 0 |
| ORACLE_VERIFIED | 0 | 0 |
| declaring v3 | 0 | 0 |
| ReferenceSolution | 1 | 1 |
| OracleExecution | 20 | 20 |
| QuestionApproval | 0 | 0 |
| CodeSubmission | 44 | 44 |
| adaptive_eligible | 0 | 0 |

## E. Grading-truth fingerprint unchanged? **YES**

```
1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
```

Byte-identical to the baseline. **The DDL was additive in fact, not just on
paper** — which is what this fingerprint exists to prove.

## F. Pre-image tables empty? **YES**

```
groups_remediationbatch      0
groups_questionpreimage      0
groups_remediationaction     0
```

## G. Dry-run successful? **YES**

```
PRE-IMAGE CAPTURE  (DRY RUN)
  database        neondb
  role            learnlm_census_ro
  production      True
  operator        Suhas

  batch           p27-pilot-1 (new)
  questions       7
```

**Zero production rows created** — confirmed after the run: batches 0,
pre-images 0, actions 0.

Note the dry-run ran as `learnlm_census_ro` and was *allowed to*: the role gate
only applies to writes (`needs_write=False` for a dry-run), so planning is
possible from the read-only role while capture is not. That is the intended
asymmetry.

## H. Dry-run membership and per-question state

All seven resolved against production. Every one is `DRAFT` / `UNVERIFIED` /
contract `v1`.

| q | cases | canonical verdict | pre-image digest | capturable |
|---|---:|---|---|---|
| 1689 | 4 | NEEDS_MIGRATION | `dec9993980494229` | yes |
| 963 | 4 | INVALID_INPUT | `06a9bb6b6d4ab57b` | yes |
| 3309 | 5 | CONTRACT_MISMATCH | `2395b94572381243` | yes |
| 17 | 4 | INVALID_INPUT | `4dd7a16898a91d27` | yes |
| 266 | 4 | NEEDS_MIGRATION | `1ba2e68f49b16317` | yes |
| 1436 | 4 | NEEDS_MANUAL_REVIEW | `0d4425883fdff4b9` | yes |
| **264** | 4 | **SAFE** (control) | `396d211e893103ce` | yes |

29 hidden test cases across the batch. The design's six failure modes plus the
control are all represented, and each digest is the exact value its pre-image
would carry.

**Freeze plan:** capture leaves the batch `OPEN`; a *separate* invocation
(`--freeze --apply --confirm`) verifies every pre-image and closes membership.
Capture does not freeze implicitly, so the batch can be reviewed in between.

## I. Blockers before the first real capture

**One, and it is an authorization decision, not a defect.**

The capture must run as a role that is neither `learnlm_census_ro` (refused —
holds no write privilege) nor `learnlm_pilot_rw` (refused — scoped to the
reference pilot). Two consequences:

1. **A role with `INSERT` on the three tables is required.** The census role
   has `SELECT` only, correctly. Either the owner performs the capture, or a
   purpose-made role is created and granted `INSERT` on the three pre-image
   tables — and *only* those.
2. **`.env`'s `POSTGRES_*` points at the census role**, so `manage.py` will
   connect as it by default. The capture invocation needs that connection
   repointed, exactly as the migration did.

The role gate is a **deny-list**, so any third role passes it automatically. If
you create one, tell me its name and I will add it to the accepted set
explicitly rather than leaving it implicitly allowed.

No per-question blocker: all seven are capturable, including the two
`INVALID_INPUT` questions whose suites hold list-valued `expected_output` —
capture stores broken data verbatim, by design and by test.

## J. Exact next action

1. Decide the capture role (owner, or a new one with `INSERT` on the three
   tables).
2. Repoint `POSTGRES_*` at it for the capture invocation.
3. Run the capture:

```bash
python manage.py preimage_capture --batch p27-pilot-1 --questions 1689 963 3309 17 266 1436 264 --purpose "remediation pilot" --operator Suhas --apply --confirm
```

4. **Stop.** Verify seven pre-images with the digests in §H, then freeze as a
   separate step, then stop again for review before any remediation.

---

## Final status

```
0044                     = VERIFIED IN PRODUCTION
PRE-IMAGE FOUNDATION     = VERIFIED
PRE-IMAGE CAPTURE        = NOT APPLIED
REMEDIATION              = NOT STARTED
RESEED                   = NO
TRANSFORMER KT           = NOT STARTED
```
