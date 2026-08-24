# P2.7 — Pilot batch frozen

**`p27-pilot-1` is frozen with 7 verified pre-images. Grading truth unchanged.**

Membership can no longer change. The batch is now the fixed referent that any
remediation and any rollback will be measured against.

---

## A. Privilege verification — as `learnlm_preimage_rw`

The column-level grant is exactly as narrow as intended:

```
groups_remediationbatch table-level:
  SELECT=T  INSERT=T  UPDATE=F  DELETE=F  TRUNCATE=F

groups_remediationbatch column-level UPDATE:
  id             F      state          T
  batch_key      F      frozen_at      T
  purpose        F      frozen_by_id   T
  created_at     F
  created_by_id  F
```

Three columns updatable, five not. Table-level `UPDATE` reads **false**, which
is correct for a column-level grant and is why this had to be checked with
`has_column_privilege` — the table-level function returns true when the role
holds `UPDATE` on *any* column, so it reports a narrow grant and a broad one
identically.

Everything else unchanged:

```
groups_questionpreimage    SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
groups_remediationaction   SELECT=T INSERT=T UPDATE=F DELETE=F TRUNCATE=F
groups_question            SELECT=T UPDATE=F
groups_questionapproval / referencesolution / oracleexecution /
codesubmission / usertopicmastery / usercodingprofile /
recommendationlog          []  (no privilege of any kind)
```

**A pre-image still cannot be altered or deleted by the role that wrote it**,
and grading truth remains unreachable.

## B. Batch state before the freeze

```
batch_key           p27-pilot-1
state               OPEN
frozen_at           NOT FROZEN
pre-images          7   [17, 264, 266, 963, 1436, 1689, 3309]
remediation actions 0
```

All seven pre-images verified and matched their live rows; q264 identical;
fingerprint identical.

## C. Freeze — **SUCCEEDED**

```
Batch p27-pilot-1 frozen with 7 member(s). Every pre-image re-verified.
Membership can no longer change.
```

Freeze re-verifies **every** pre-image before closing membership — a batch is
only worth freezing if what it holds still restores.

## D. Final frozen state

| field | value |
|---|---|
| `batch_key` | `p27-pilot-1` |
| `state` | **CAPTURED** |
| `frozen_at` | `2026-08-17 05:32:58 UTC` |
| `frozen_by` | `Suhas` |
| `purpose` | remediation pilot |
| `created_by` | `Suhas` |
| `created_at` | `2026-08-17 05:19:12` |

`CAPTURED` is this model's frozen state — the name records *what happened*
(capture completed), with `frozen_at` as the fact that membership closed.

**The freeze changed only the three intended fields.** `batch_key`, `purpose`
and `created_by` were compared before and after and are unchanged. The
column-level grant made anything else impossible, but "could not have" and
"did not" are different claims, and only the second is checkable.

## E. Membership — exactly 7, unchanged

```
[17, 264, 266, 963, 1436, 1689, 3309]
```

**0 remediation actions** — freeze records none, correctly. Actions belong to
remediation and rollback.

## F. Digest verification after freeze — full 64 characters

```
q17     4dd7a16898a91d27098d5256d4ef6486a9b63d7ea9b2f631d379bc6732d4213a
q264    396d211e893103ceca7188a0c896458cbc2f62422703fe75f8a25981dcc80271
q266    1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
q963    06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
q1436   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
q1689   dec9993980494229140a983f7007ceed8b45d60a6d33b6527d7fefc198fb080c
q3309   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
```

Each checked three ways: recomputes from its own stored bytes, passes
`verify()`, and equals the live question's digest. Identical to capture and to
both dry-runs.

Verified through `learnlm_census_ro` — the role that wrote is not the role that
checked.

## G. q264 control — identical to its pre-image

All seven captured fields byte-identical. The control has now passed through
capture and freeze untouched.

## H. Grading-truth fingerprint

```
1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f   IDENTICAL
```

All nine counts unchanged.

## I. Unexpected changes — **NONE**

## J. Exact next step — HUMAN REVIEW, then STATEMENT REPAIR

The pre-image foundation is complete: every question in the batch can be
restored to a digest-verified prior state, atomically, by a role that cannot
touch anything else.

**Remediation is still not authorised to start**, and the ordering established
in the pipeline design is not negotiable:

1. **Human review of the seven** — `preimage_inspect` is read-only and safe to
   run at any time.
2. **`STATEMENT_REPAIR` first.** 9 of 20 audited statements were defective, and
   3 of these 7 are in that class (963, 1689, and 1436's constraint violation).
   Deriving keys — by oracle or otherwise — from a statement that contradicts
   its own title, example or key produces confidently wrong answers with fresh
   provenance. **No key may be touched until the statement it answers is
   settled**, and that is human adjudication, not a command.
3. `CONTRACT_REPAIR` for 3309 and the two `INVALID_INPUT` questions, then
   oracle re-derivation of keys, then the quality gate, then approval, then
   promotion.

Rollback is available and untested against real data — the first remediation
batch is also the first genuine exercise of it.

---

## Final status

```
PRE-IMAGE CAPTURE = COMPLETE (7 pre-images, all verified)
BATCH             = FROZEN (state CAPTURED, membership closed)
REMEDIATION       = NOT STARTED
RESEED            = NO
TRANSFORMER KT    = NOT STARTED
```
