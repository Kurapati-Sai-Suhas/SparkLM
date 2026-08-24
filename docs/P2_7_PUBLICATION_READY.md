# P2.7 — q3309 publication: preflight clean, nothing applied

The publication edge was already built in P2.7h-8; this phase verified it,
found and closed one real gap in it, and ran the preflight against production
through the real role.

**LEGAL — PENDING_REVIEW → PUBLISHED may be applied.** Nothing was applied.
q3309 is still `PENDING_REVIEW`, `adaptive_eligible=false`, and zero questions
in the bank are PUBLISHED.

---

## 1. The lifecycle, confirmed from the code

`Question.STATUS_TRANSITIONS` — three edges, unchanged:

```
DRAFT ──> PENDING_REVIEW ──> PUBLISHED
              <────────────────┘
```

`PENDING_REVIEW → PUBLISHED` is legal. Its prerequisites, as implemented in
`_publication_blockers` and re-derived at preflight time:

- `trust_state == ORACLE_VERIFIED`
- a `QuestionApproval` exists and is **stamped as promoted**
- the canonical reference is the one the approval names, at the same
  `source_hash`  ← **added this phase, see §4**
- the artifact rebuilt from live state has **no blockers** (every case
  oracle-backed, ≥2 agreeing runs, no conflict, no nondeterminism, contract
  known)
- the rebuilt digest still equals `approval.artifact_digest`
- the frozen quality verdict on the approval is a PASS

No new prerequisites were invented. The one addition is a check `question_promote`
already performs and publication was missing.

## 2. The role, verified on production

`learnlm_status_rw` exists now. Read as census, every column checked
individually:

```
attributes  super=False createrole=False createdb=False replication=False
            bypassrls=False login=True     memberships: none

groups_question UPDATE by column
  id False · title False · content False · base_difficulty False
  topic_id False · hidden_test_cases False · boilerplate_code False
  hidden_wrapper_code False · execution_contract_version False
  status TRUE · trust_state False

required probe          UPDATE on groups_question.status: True
forbidden privileges actually held: NONE
```

`NONE` covers the whole deny-list: trust_state, every other question column,
INSERT/UPDATE on the approval, any write on the reference or executions,
UPDATE/DELETE on the audit trail, and any write on the pre-image tables. The
existing command and role were reused; no second writer and no second role were
created.

The preflight itself ran **through `--alias status`**, so the grant list was
exercised end to end on production: the role's SELECTs on the question, batch,
pre-image, approval, reference and executions are exactly what the publication
chain needs, and nothing more.

## 3. Preflight (production, read-only)

```
STATUS TRANSITION  (DRY RUN)
  database neondb   role learnlm_status_rw   production True   operator Suhas

  batch           p27-pilot-1 (CAPTURED)
  question        3309 — Find the Index of the First Occurrence in a Stri
  pre-image       2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
  current digest  e7fbc767e16eebdd29314a192d5d0b928d1017925ff082a6933c354f5efbc929
  projected after a88a4e191e368f8d2a31b82b802ea3f8ea7c9315b9ae8bf3e6270309463bddd8

  current status  PENDING_REVIEW      proposed status PUBLISHED

  evidence re-derived for publication:
    approval              #1 by user 1 at 2026-08-19 17:07:13 UTC
    promoted              2026-08-20 06:36:59 UTC by user 1
    approved digest       b1df39f5d0aa73088d8750ab643f6ef2eb16a16c345b85966df2498d46d4e46c
    recomputed digest     b1df39f5d0aa73088d8750ab643f6ef2eb16a16c345b85966df2498d46d4e46c
    digests match         True
    oracle-backed cases   12/12
    agreeing runs         [2]
    artifact blockers     none
    quality gate          tier1 1.0 / tier2 1.0 — PASS
    canonical reference   #2 APPROVED active=True
    reference hash        18ad8f390642c315ef78e52fecaf97ab31fd6fc69fca2a02b3114e03d77a341b
    == approved hash      True

  fields that would change:
    groups_question.status   PENDING_REVIEW → PUBLISHED
  fields guaranteed unchanged:
    content · trust_state · execution_contract_version · boilerplate_code
    hidden_wrapper_code · hidden_test_cases
    groups_questionapproval / groups_referencesolution /
    groups_oracleexecution   (no write privilege on this role)

  trust and eligibility:
    trust_state             ORACLE_VERIFIED (unchanged)
    adaptive eligible now   False
    adaptive eligible after True

LEGAL — PENDING_REVIEW → PUBLISHED may be applied.
DRY RUN — nothing was written.
```

Every item the brief asked the preflight to re-check is there and passing.

**Two notes on digests.** The question digest is now `e7fbc767…`, not the
`1a2e3115…` I predicted last phase — that prediction covered the status change
alone, and promotion then moved `trust_state`, which is also a captured field.
The prediction was right for its step and stale one step later. The **artifact**
digest is unaffected by either (`b1df39f5…`), which is why approval #1 is still
valid.

## 4. What this phase changed

**A gap in the publication edge.** It compared the rebuilt artifact digest to
the approval, but never checked the live canonical reference against the
approval's `reference_id` and `reference_source_hash` — the check
`question_promote` performs as its gate 4a. The digest catches a reference whose
*source* moved (the live hash is inside the artifact) but cannot distinguish
"this approval was granted against a different reference" from "these are the
same". Publication is the act that makes the answers count, so it now says which
of the two it means. Two new blockers, two new tests, two new mutants.

**The preflight now shows its work.** `_publication_blockers` returns what it
re-derived, and the plan prints it. A preflight that silently passes tells an
operator only that nothing was wrong, which is not the same as telling them what
is true — and this is the last gate before a question starts teaching learner
models.

## 5. Tests

`groups/test_status_transition.py` — **75 tests, all passing** (was 64).

New this phase: retired reference refused; reference revision changed since
approval refused; a different canonical reference refused; conflicting evidence
refused; one case's evidence deleted refused; a single agreeing run refused; the
suite drifting after promotion refused; publication leaves trust, the approval,
the reference and every execution untouched; the preflight shows the evidence
chain; the first edge shows none; an unknown alias fails loudly with no fallback.

Already present and still passing: the legal edge, DRAFT → PUBLISHED refused as
illegal, unverified refused, unpromoted approval refused, stale digest refused,
missing evidence refused, wrong role refused on production, over-granted role
refused, only `status` changes, row lock, transaction, two concurrent-drift
races, dry-run writes nothing.

## 6. Mutation

38 mutants (33 from P2.7h-8 plus 5 for the publication edge).

```
37 killed / 38
real survivors: 0
  E1  EQUIVALENT: capitalisation in a success message
```

The five new ones, all killed: publication accepting a reference nobody
approved; accepting a reference *revision* nobody approved; accepting a retired
or non-canonical reference; rebuilding the artifact on the default connection;
and substituting a fresh passing quality verdict for the approved frozen one.

## 7. Regression

```
2582 passed, 2 warnings in 245.04s
```

**Infrastructure, reported separately:** none this phase. (The Docker outage and
the killed-sweep residue were both in P2.7h-8 and are described there.)

## 8. Production safety

```
q3309   PENDING_REVIEW / ORACLE_VERIFIED   adaptive_eligible False
        digest e7fbc767…   approval #1 promoted   OracleExecution 24
PUBLISHED anywhere in the bank: 0
status values present: ['DRAFT', 'PENDING_REVIEW']
STATUS_TRANSITION actions: 1        total remediation actions: 13
submissions marked adaptive_eligible: 0
bank fingerprint: 950b98500023764724f51a9d960af2550c5e9ee7f9e93dc1add79c4ce887515c
```

The fingerprint moved from `9cc3c8d8b0…` because of the two writes you applied
between phases — the status transition and the promotion, both of which change
captured columns. Nothing in this phase wrote anything.

## 9. The command, when you want it

```bash
python manage.py question_status --alias status --batch p27-pilot-1 --question 3309 --to PUBLISHED --digest e7fbc767e16eebdd29314a192d5d0b928d1017925ff082a6933c354f5efbc929 --reason "<why>" --operator Suhas --apply --confirm
```

Re-run the preflight first: if anything moved, the digest handshake will refuse
before the gates do. After it lands, q3309 becomes the first question in the
bank whose submissions may teach the adaptive model — `adaptive_eligible` is
read and frozen at submission time, so only submissions recorded from that
moment on are affected, and every past submission stays inert.
