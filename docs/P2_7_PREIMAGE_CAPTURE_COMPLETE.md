# P2.7 — Pilot pre-image capture complete

**Seven pre-images captured. Batch OPEN. Grading truth unchanged.**

The first production write of this milestone. It wrote only pre-image rows, and
the role it ran as was incapable of writing anything else.

---

## A. Capture-role identity

```
endpoint          ep-blue-hat-aj7p2x8v-pooler
current_database  neondb
current_user      learnlm_preimage_rw
session_user      learnlm_preimage_rw
server_version    17.10 (29ad1b7)
```

Via the dedicated `preimage` alias. Neither `neondb_owner`,
`learnlm_census_ro` nor `learnlm_pilot_rw` was used for any write.

## B. Privileges — self-checked by the writing role

```
groups_remediationbatch    SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
groups_questionpreimage    SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
groups_remediationaction   SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F

groups_question            SELECT=T INSERT=F UPDATE=F DELETE=F TRUNCATE=F
```

No privilege of any kind on `groups_questionapproval`,
`groups_referencesolution`, `groups_oracleexecution`, `groups_codesubmission`,
`groups_usertopicmastery`, `groups_usercodingprofile`,
`groups_recommendationlog`.

**The write could not have touched grading truth**, whatever the code did.

## C. Pre-capture baseline — verified before writing

All nine counts exact, pre-image tables empty, fingerprint
`1981a621…8246f` identical.

## D. Dry-run — digests matched the earlier run exactly

Seven questions resolved, all capturable, zero writes.

## E. Capture result — **SUCCEEDED**

```
Captured 7 pre-image(s) into p27-pilot-1. Every one verified.
Membership is NOT frozen.
No question was modified by this command.
```

## F. Rows created

| table | rows |
|---|---:|
| `RemediationBatch` | **1** — `p27-pilot-1`, state `OPEN`, purpose "remediation pilot", created_by `Suhas` |
| `QuestionPreImage` | **7** |
| `RemediationAction` | **0** — capture records no action; actions belong to remediation and rollback |

Membership: `[17, 264, 266, 963, 1436, 1689, 3309]` — exactly the seven
requested, nothing else.

## G. Digest verification — full 64 characters, not prefixes

```
q17     4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
q264    396d211e893103ceca7188a0c896458cbc2f62422703fe75f8a25981dcc80271
q266    1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
q963    06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
q1436   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
q1689   dec9993980494229140a983f7007ceed8b45d60a6d33b6527d7fefc198fb080c
q3309   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
```

Every prefix matches the dry-run. Each was checked three ways: the stored
digest recomputes from its own stored bytes, `verify()` passes, and it equals
the live question's digest.

Verified through `learnlm_census_ro` — **the role that wrote is not the role
that checked.**

## H. q264 control — field by field

| field | result |
|---|---|
| `content` | identical |
| `status` | identical |
| `trust_state` | identical |
| `execution_contract_version` | identical |
| `boilerplate_code` | identical |
| `hidden_wrapper_code` | identical |
| `hidden_test_cases` | identical |

`captured_by=Suhas`, `was_adaptive_eligible=False`. The control is captured and
untouched.

## I. Grading-truth fingerprint

```
1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f   IDENTICAL
```

## J. No Question changed — **CONFIRMED**

All nine counts unchanged; fingerprint identical; the writing role holds no
write privilege on `groups_question`.

## K. Batch state — **OPEN**, not frozen

## Two defects found and fixed before the write

**1. Cross-alias foreign keys.** The first capture attempt failed:

```
ValueError: Cannot assign "<User: Suhas>": the current database router
prevents this relation.
```

The operator is resolved on `default` (census) while rows are written on
`preimage`. Django's router treats two aliases as two databases and refuses to
relate objects across them — even though both point at the *same* Neon
database. Fixed by assigning the foreign key **by id** (`captured_by_id`),
which states the same fact without consulting the router, and has the useful
side effect that the capture role needs no read access to the user table.

The failed attempt wrote nothing: the alias-scoped transaction rolled the batch
back, verified afterwards as 0/0/0.

**2. The test suite could have reached production.** `settings.py` creates the
`preimage` alias whenever `PREIMAGE_USER` is set — including under pytest,
where it would have pointed at **production with INSERT rights**. The isolation
plugin redirected `POSTGRES_*` but knew nothing about `PREIMAGE_*`.

Fixed in `sparklm_test_isolation.py` by blanking those five variables before
Django loads. *Blanking*, not deleting: `load_dotenv` skips a key already
present but happily restores an absent one, so a popped variable would have
come back from `.env` moments later. A test asserts the alias does not exist
under pytest.

This one is worth dwelling on — it was created by my own change two turns ago
and would not have failed loudly. It would simply have been true.

**Full regression: 2,088 passed, 0 failed, 0 errors.**

## L. Exact next action — FREEZE + REVIEW

The batch is deliberately **OPEN** so the seven pre-images can be reviewed
before membership is closed.

1. **Review** — `python manage.py preimage_inspect --batch p27-pilot-1 --operator Suhas`
   (read-only, runs as the census role).
2. **Freeze**, when satisfied — a separate invocation, which re-verifies every
   pre-image before closing membership:

```bash
python manage.py preimage_capture --alias preimage --batch p27-pilot-1 --freeze --operator Suhas --apply --confirm
```

3. **Stop again.** Remediation requires a frozen batch, and the first
   remediation class is `STATEMENT_REPAIR` — which needs human adjudication
   before any key is touched.

---

## Final status

```
PRE-IMAGE CAPTURE = COMPLETE (7 pre-images, all verified)
BATCH             = OPEN
REMEDIATION       = NOT STARTED
RESEED            = NO
TRANSFORMER KT    = NOT STARTED
```
