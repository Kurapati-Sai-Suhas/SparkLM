# P2.7 — Pilot capture: stopped before writing

**The capture did not run.** Two blockers surfaced during the pre-write checks,
one of them a defect in my own code. Both are now understood; one needs a grant
from you.

Production untouched: fingerprint identical, zero pre-image rows.

---

## A. Capture role identity — **VERIFIED**

Connected via a new `preimage` alias, as the role itself:

```
endpoint          ep-blue-hat-aj7p2x8v-pooler
current_database  neondb
current_user      learnlm_preimage_rw
session_user      learnlm_preimage_rw
server_version    17.10 (29ad1b7)
```

Neither `neondb_owner`, `learnlm_census_ro` nor `learnlm_pilot_rw` was used.

## B. Privilege scope — **VERIFIED, self-checked**

The role checked its own privileges rather than another role checking on its
behalf:

```
groups_remediationbatch    SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
groups_questionpreimage    SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
groups_remediationaction   SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
```

No privilege of any kind on `groups_question`, `groups_questionapproval`,
`groups_referencesolution`, `groups_oracleexecution`, `groups_codesubmission`,
`groups_usertopicmastery`, `groups_usercodingprofile`,
`groups_recommendationlog`.

## C. Pre-capture baseline — **UNCHANGED**

All nine counts exact; pre-image tables empty from the capture role's own view;
fingerprint `1981a621…8246f` identical.

## D. Capture result — **NOT RUN**

### Blocker 1 — the role cannot read the question it must copy

```
django.db.utils.ProgrammingError: permission denied for table groups_question
```

A pre-image **is** a copy of a question row: `content`, `status`,
`trust_state`, `execution_contract_version`, `boilerplate_code`,
`hidden_wrapper_code`, `hidden_test_cases`. Capture cannot produce one without
reading the source.

The grant spec said "zero privilege on `groups_question`", which is exactly
right for *writes* and one step too far for reads. Required:

```sql
GRANT SELECT ON groups_question TO learnlm_preimage_rw;
```

**Read only.** The essential property is untouched — no `INSERT`, `UPDATE`,
`DELETE` or `TRUNCATE` on grading truth, so a capture still cannot alter
anything. This is the minimum that makes capture possible at all.

Nothing else needs widening: the operator lookup and the fingerprint read both
run on the census connection, not this one.

### Blocker 2 — a defect in my own code, now fixed

`groups/pre_image.py` used the default manager for **every** write, while
`preimage_capture --alias preimage` created the batch on the chosen alias. So
the batch would have been written on one connection and the pre-images
attempted on another.

Here it failed loudly, because `default` is the read-only census role. On a
deployment where `default` happened to be writable, **rows would have landed
silently on the wrong database, with a batch pointing at pre-images that were
not there** — precisely the failure this tooling exists to prevent.

I found it by running the real command as the real capture role. The local
tests never caught it because they use a single database, where "wrong alias"
and "right alias" are the same connection.

Fixed by threading the alias from the batch instance through every write:

- `alias_of(batch)` resolves it once, from `_state.db`
- capture, freeze, `require_pre_image`, `record_action` and rollback all route
  through `.using(alias)`
- **`transaction.atomic(using=alias)`** replaces the bare decorators — a bare
  `@transaction.atomic` opens the transaction on `default` while the writes go
  elsewhere, which would have silently broken the all-or-nothing rollback
  guarantee
- regression test asserts every write names the batch's own alias

## E–H. Rows created, digests, q264, post-capture fingerprint

Nothing was created; digests remain the dry-run expectations. Production
re-verified after all work:

```
fingerprint  1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f  IDENTICAL
groups_remediationbatch      0
groups_questionpreimage      0
groups_remediationaction     0
```

## I. No grading truth changed — **CONFIRMED**

All nine counts and the fingerprint unchanged. The capture role cannot write
grading truth, and no write was attempted.

## J. Batch state — **does not exist**

`p27-pilot-1` was never created.

## Also changed this phase

- **`settings.py`** — new `preimage` alias, present only when `PREIMAGE_USER`
  is set. A separate alias rather than repointing `default`, because that
  difference *is* the safety property. Absent the variable the alias does not
  exist and the commands fail loudly instead of falling back to the census
  role and failing mid-batch.
- **`docs/FEATURE_FLAGS.md`** — the five `PREIMAGE_*` variables documented. The
  repo's own guard caught them undocumented, which is the guard working.

**Full regression: 2,087 passed, 0 failed, 0 errors.**

## K. Exact next action

One grant, then I can capture:

```sql
GRANT SELECT ON groups_question TO learnlm_preimage_rw;
```

Verify with:

```bash
psql "$OWNER_URL" -c "select has_table_privilege('learnlm_preimage_rw','groups_question','SELECT') as can_read, has_table_privilege('learnlm_preimage_rw','groups_question','UPDATE') as can_write;"
```

Expect `can_read = t`, `can_write = f`.

Then I will re-run the pre-write checks, dry-run once more, and capture:

```bash
python manage.py preimage_capture --alias preimage --batch p27-pilot-1 --questions 1689 963 3309 17 266 1436 264 --purpose "remediation pilot" --operator Suhas --apply --confirm
```

and stop before freeze, as instructed.

---

## Final status

```
PRE-IMAGE CAPTURE   = NOT APPLIED (blocked: read grant on groups_question)
BATCH               = DOES NOT EXIST
ALIAS ROUTING BUG   = FOUND AND FIXED
REMEDIATION         = NOT STARTED
RESEED              = NO
TRANSFORMER KT      = NOT STARTED
```
