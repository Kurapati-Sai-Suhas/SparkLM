# P2.7 — Pre-image write role verified; capture blocked on credentials

**`learnlm_preimage_rw` audits clean.** The capture did not run: its credentials
are not present in this environment, so no connection as that role is possible
from here.

Grading truth unchanged. Zero pre-images captured. RESEED = NO.

---

## A. Role security attributes — **PASS**

| attribute | value | expected |
|---|---|---|
| LOGIN | `True` | True |
| SUPERUSER | `False` | False |
| CREATEDB | `False` | False |
| CREATEROLE | `False` | False |
| REPLICATION | `False` | False |
| BYPASSRLS | `False` | False |
| INHERIT | `True` | — |
| VALID UNTIL | no expiry | — |

**Roles this role is a member of: NONE.** It inherits no privilege from
anything — not `neondb_owner`, not any broad write role.

`neondb_owner` is a member *of* `learnlm_preimage_rw`, which is the normal Neon
pattern: it lets the owner `SET ROLE` into it. It grants this role nothing, and
is the opposite direction from the one that would matter.

## B. Exact write privileges — **PASS**

| table | privileges |
|---|---|
| `groups_remediationbatch` | `SELECT`, `INSERT` |
| `groups_questionpreimage` | `SELECT`, `INSERT` |
| `groups_remediationaction` | `SELECT`, `INSERT` |

No `UPDATE`, `DELETE` or `TRUNCATE` on any of them. Exactly the scope intended:
the role can *append* pre-images and can never alter or remove one — which is
the same immutability the model enforces in Python, now enforced by the server
as well.

## C. Forbidden write surfaces — **PASS, and stronger than asked**

Zero privileges of **any** kind — not even `SELECT`:

```
groups_question            []
groups_questionapproval    []
groups_referencesolution   []
groups_oracleexecution     []
groups_codesubmission      []
groups_usertopicmastery    []      (checked in addition)
groups_usercodingprofile   []      (checked in addition)
groups_recommendationlog   []      (checked in addition)
```

DDL surface:

```
CONNECT           True
USAGE on public   True
database CREATE   False
schema CREATE     False
objects owned     0
```

**A capture performed with this role physically cannot touch grading truth.**
That is a much better property than "the code does not try to".

### A correction to my own audit method

My first pass reported the opposite — "missing SELECT/INSERT on all three
tables" and "no writes on grading truth" — and **both readings were wrong for
the same reason.**

`information_schema.table_privileges` lists only privileges "where the grantor
or grantee is a currently enabled role". Queried as `learnlm_census_ro`, grants
made to a *different* role are invisible. So it reported `NONE` everywhere:
the missing grants were a false alarm, and the clean forbidden surfaces were
not evidence at all — the dangerous direction.

`has_table_privilege(role, table, privilege)` evaluates the effective privilege
regardless of who asks. Every result above uses it. This is the same class of
mistake as the earlier `information_schema.tables` visibility issue, and
`gate_write_privilege` has now been switched to `has_table_privilege` too,
where it had the same latent bug.

## D. Production target identity — **NOT VERIFIED AS THIS ROLE**

Steps 3 and 5 require connecting *as* `learnlm_preimage_rw`. There are no
`PREIMAGE_*` credentials in `.env` — the keys present are `POSTGRES_*`
(census), `PILOT_*`, and the placeholder `AZURE_POSTGRES_*`.

The target was verified from the census role and is unchanged:
`neondb` @ `ep-blue-hat-aj7p2x8v-pooler`, PostgreSQL 17.10, migration
`0044_pre_image_rollback` applied 2026-08-16 18:52:19.

## E. Pre-capture integrity baseline — **UNCHANGED**

All nine counts exact (2926 / 0 / 0 / 0 / 1 / 20 / 0 / 44 / 0) and:

```
fingerprint  1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
baseline     1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
IDENTICAL
```

## F. Capture success — **NOT RUN**

Blocked at Step 3. No connection as the capture role is possible from here.

## G. Batch / pre-image counts

```
groups_remediationbatch      0
groups_questionpreimage      0
groups_remediationaction     0
```

## H–I. Digest and q264 verification — pending capture

The dry-run digests stand as the expected values:

```
1689 dec9993980494229   963  06a9bb6b6d4ab57b   3309 2395b94572381243
17   4dd7a16898a91d27   266  1ba2e68f49b16317   1436 0d4425883fdff4b9
264  396d211e893103ce   <- SAFE control
```

## J. Post-capture fingerprint — unchanged (no capture occurred)

## K. Unexpected production changes — **NONE**

## Hardening applied this phase

Since you specified that `neondb_owner` must not perform these writes, I made
that enforceable rather than procedural:

**The role gate is now an ALLOW-list.** Previously it named the two roles to
refuse, which let every *other* role through — including `neondb_owner`, which
can write every table in the database. That made the purpose-built
least-privilege role decorative: the capture would have run with the privilege
to rewrite grading truth, with only the operator's memory preventing it.

- `ALLOWED_WRITE_ROLES = {"learnlm_preimage_rw"}`
- `neondb_owner` refused with a specific reason; any unreviewed role refused by
  default
- the allow-list applies to **production only** — a local test database uses
  whatever role that throwaway instance has, and demanding the production role
  there would make the gates untestable, which is how gates end up unverified
- `gate_write_privilege` switched to `has_table_privilege` (same latent bug as
  the audit)

**Mutation: 19/19 killed**, one planted equivalent. Two mutants were re-aimed at
the rewritten gate — the old M1 disabled only the specific-message branch, after
which the generic refusal still fired, so it survived *correctly*.

**Full regression: 2,085 passed, 0 failed, 0 errors.**

## L. Exact next step before capture

**Add the role's credentials to `.env` yourself** — do not send them to me, and
do not paste them into this conversation. The file is gitignored:

```
PREIMAGE_DB=neondb
PREIMAGE_USER=learnlm_preimage_rw
PREIMAGE_PASSWORD=...
PREIMAGE_HOST=ep-blue-hat-aj7p2x8v-pooler.<...>
PREIMAGE_PORT=5432
```

Then tell me, and I will wire the alias and run:

```bash
python manage.py preimage_capture --batch p27-pilot-1 --questions 1689 963 3309 17 266 1436 264 --purpose "remediation pilot" --operator Suhas --apply --confirm
```

Alternatively, run that yourself with `POSTGRES_*` repointed at the role, and I
will verify the result.

Either way the capture will now refuse to proceed as anything other than
`learnlm_preimage_rw` on production.

---

## Final status

```
PRE-IMAGE WRITE ROLE     = VERIFIED (correctly scoped, cannot touch grading truth)
PRE-IMAGE CAPTURE        = NOT APPLIED (blocked: credentials absent)
BATCH FREEZE             = NOT APPLIED
REMEDIATION              = NOT STARTED
RESEED                   = NO
TRANSFORMER KT           = NOT STARTED
```
