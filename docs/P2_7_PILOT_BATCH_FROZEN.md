# P2.7 — Migration 0049 applied; pilot batch frozen

**Phase 14B · M2 P2.7h-29 · production checkpoint**

Status: **migration applied and verified**, **`p27-pilot-2` frozen with 4
pre-images**. No question row was written. The reseed has not started.

---

## 1. Migration 0049 — applied and verified

Applied by the operator with the owner credential (`neondb_owner`); no
`learnlm_*` role can write `django_migrations`.

```
head            0049_reseed_contract_stage   applied 2026-08-25 05:42:24 UTC
migrate --check exit 0 — nothing outstanding
```

Every value compared against the baseline captured **before** the apply:

| | pre | post | |
|---|---|---|---|
| `action_class` column | `varchar(32)` | `varchar(32)` | unchanged |
| `stage` column | `varchar(24)` | `varchar(24)` | unchanged |
| Question | 2926 | 2926 | unchanged |
| RemediationAction | 17 | 17 | unchanged |
| QuestionPreImage | 7 | 7 | unchanged |
| RemediationBatch | 1 | 1 | unchanged |
| ReseedLedger | 0 | 0 | unchanged |
| ReferenceSolution / OracleExecution / QuestionApproval | 3 / 70 / 2 | 3 / 70 / 2 | unchanged |
| bank fingerprint | `3c886bc4…f49f6` | `3c886bc4…f49f6` | unchanged |
| action rows sha256 | `2b16c456…1068` | `2b16c456…1068` | unchanged |

The no-op prediction from Phase 13 is now an **observation**, not a forecast.
The 69 migration-safety tests still pass against the applied state.

---

## 2. Pilot batch `p27-pilot-2`

Pre-flight re-run immediately before capture: **52/52 checks** across the four
questions — DRAFT/UNVERIFIED, no cases, no pre-image, no reference, execution,
approval or remediation action, placeholder present, still variadic, contract
v1, no custom wrapper, `statement_blockers == []`.

```
batch      p27-pilot-2   CAPTURED (frozen)
role       learnlm_preimage_rw        production True
operator   Suhas
members    4

  q1940   digest=b0526cf63e43219a   cases=0
  q1974   digest=793d6dbd90cee5db   cases=0
  q2057   digest=5b05dfcd47a28113   cases=0
  q2290   digest=7df92ef0d5d22122   cases=0
```

Capture and freeze were **separate invocations**, so the membership was
inspected before it became permanent. Every pre-image was verified on capture
and re-verified on freeze; each digest matches live state now, with zero
differing fields.

**q2027 is excluded.** It has no pre-image in any batch and is untouched —
DRAFT/UNVERIFIED, 0 cases, 0 actions. Its rejected artifact and the findings
behind that rejection are preserved for the later example-generation
investigation, not deleted and not regenerated.

---

## 3. q1974 carries the newly approved specification

```
new digest        eb8458fd460a64b62bc129e0662f3af509523dace43db127afdb0e90d01ffc84
superseded        ad2a9eb09a60a7a85d2e0daf04bc21a1bb6d661ab5a7acf87c068d6899502a0f
semantic change   NONE
```

Both the specification file and the freeze file carry the new digest; the old
one is retained as `superseded_digest` with a `revision_log` entry. The
approved text was not silently modified — old and new are both on record.

---

## 4. The exact production delta

| table | before | after | delta |
|---|---|---|---|
| `groups_remediationbatch` | 1 | 2 | **+1** |
| `groups_questionpreimage` | 7 | 11 | **+4** |
| `groups_question` | 2926 | 2926 | 0 |
| `groups_remediationaction` | 17 | 17 | 0 |
| `groups_reseedledger` | 0 | 0 | 0 |
| references / executions / approvals | 3 / 70 / 2 | 3 / 70 / 2 | 0 |

### On the bank fingerprint

The brief anticipated the fingerprint changing to reflect the new batch. **It
did not, and it must not.**

The fingerprint is computed over `groups_question` rows only — id, md5 of
`hidden_test_cases`, md5 of `content`, status, trust_state, contract version.
A batch and its pre-images live in different tables entirely. Adding them
cannot move it, and a fingerprint change at this step would have meant a
question row had been touched — precisely the thing this checkpoint exists to
rule out.

```
bank fingerprint  3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6
                  UNCHANGED  —  QUESTION WRITES = 0
```

The two digests that *should* be stable across this checkpoint both are: the
bank fingerprint and the action-rows digest.

---

## 5. State at the checkpoint

```
MIGRATION 0049 = APPLIED + VERIFIED
q1974 REVISION = APPROVED + RE-FROZEN
q2027 PILOT    = EXCLUDED (untouched, rejection preserved)
PILOT BATCH    = p27-pilot-2 / FROZEN
PILOT QUESTIONS= [1940, 1974, 2057, 2290]
PRE-IMAGES     = 4/4, all verified against live state
QUESTION WRITES= 0
RESEED         = NOT STARTED
```

Nothing was reseeded. No statement written, no signature declared, no contract
set, no hidden test created, no quality gate, no oracle, no approval, no
promotion, no publication.

---

## 6. What the pilot runs next, when authorised

Against `p27-pilot-2`, in this order, one question at a time:

1. `reseed_statement` — writes `content` (`remediate` alias)
2. `declare_signature` — writes `boilerplate_code` (`boilerplate` alias)
3. `reseed_contract` — writes `execution_contract_version` (`contract` alias)

Expected contracts, computed offline from the declared signatures and written
nowhere yet: **q1974 → v3**, the other three → **v1** (audited as a decision
even though the field does not move).

Steps 1–3 are all reversible from the pre-images captured here. The first
irreversible step is the oracle, several stages later.

Artifacts are ready for q1974 (`artifacts-v4`, clean on all four gates). The
other three still need artifacts generated against the frozen batch — the
existing ones were built before `p27-pilot-2` existed and are stamped
`applicable: false`.
