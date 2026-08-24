# P2.7 — Python runtime switched, q1436 published, reseed preflighted

The Judge0 blocker is closed by a **selection, not an upgrade**: the same
deployment already served Python 3.11.2. q1436 now has a genuine quality-gate
PASS, real oracle evidence, an approval, a promotion and a publication.

**No bulk reseed was performed.** The preflight found that "reseed" means
something significantly more dangerous than the name suggests.

---

## The runtime switch

`common/languages.py` — one line, environment-overridable:

```python
Language("python", "Python", PYTHON_JUDGE0_ID, "py", ())   # 92 = Python 3.11.2
```

`JUDGE0_PYTHON_LANGUAGE_ID` defaults to 92; a non-integer value raises at
import rather than falling back to an interpreter nobody asked for. Rollback is
`JUDGE0_PYTHON_LANGUAGE_ID=71`, an environment variable rather than a deploy.

**Why one line reaches everything:** every Python execution path — learner
submissions (`coding_views.py:380,434`), oracle (`oracle_execute.py:161`),
quality gate (`quality_gate.py:179`), hidden-test reconciliation
(`reconcile_hidden_tests.py:68`) — resolves the id from `LANGUAGE_IDS`. A
repo-wide scan confirms **no module hardcodes a Judge0 language id** (a test now
enforces that). `execution_contract` governs argument binding and resource
limits only and never reads the id — also now asserted by test.

**Live verification before and after:**

```
PEP 585 probe            status 3
q3309 reference          12/12 stored answers reproduced      (also 12/12 under 3.8)
q1436 reference          13/13 stored answers reproduced      (0/13 under 3.8)
canary t2-single-successor-traversal   0 crashes, killed by no case → EQUIVALENT
real mutant t1-last-edge-destination   0 crashes, killed by cases 6, 7, 12
regression               2603 passed
```

The canary is the whole point. Last run it reported `KILLED case 1`; a provably
equivalent program cannot be killed, which is how the false PASS was caught.

## Provenance gap, closed

`OracleExecution.language` said `"python"` and nothing else, so 3.8 evidence was
indistinguishable from 3.11 evidence. New executions now record:

```json
{"limits": {...}, "operator": "Suhas",
 "judge0_language_id": 92, "runtime": "Python (3.11.2)"}
```

The version string is **asked of Judge0**, not hardcoded, and omitted rather
than guessed if the lookup fails.

- **No migration.** `executor` is `models.JSONField(default=dict)`; adding keys
  changes no schema.
- **No digest change.** `QuestionArtifact._frames()` — the definition of what an
  approval covers — does not read `executor`. Verified by test, so q3309's
  approval #1 remained valid throughout.
- **No historical rewrite.** A database trigger from migration 0041 makes every
  column except `is_authoritative` immutable after insert. q3309's 24 executions
  keep their original payload; their equivalence under both interpreters is
  recorded here rather than backfilled.
- `provenance_schema_version` was **not** bumped: it is written but never
  compared anywhere in production code, and bumping it would require a migration
  for no reader. Available if you want the distinction made explicit.

## q1436 lifecycle — complete

| stage | result |
|---|---|
| quality gate | **PASS** — tier1 1.0, tier2 1.0, canary EQUIVALENT, kills spread across cases 1, 5, 6, 8 |
| oracle | 26 executions (13 × 2), all SUCCESS, 13/13 ORACLE_BACKED, 0 blockers |
| review | "No blockers. This artifact is approvable." digest `e4d4362e…` |
| approval | **#2**, dry-run READY first |
| status | DRAFT → PENDING_REVIEW (`4f574880…` → `bc12f46b…`) |
| promotion | **ORACLE_VERIFIED**, preflight PROMOTABLE first |
| publication | PENDING_REVIEW → PUBLISHED (`0a9a2ee9…` → `e8294264…`) |

Every stage was dry-run first, with a digest handshake, through its own
least-privilege role (`oracle`, `approve`, `status`, `promote`).

**Final state**

```
q3309  PUBLISHED / ORACLE_VERIFIED  adaptive=True  12 cases  ref#2  24 exec  12/12 backed
q1436  PUBLISHED / ORACLE_VERIFIED  adaptive=True  13 cases  ref#3  26 exec  13/13 backed
PUBLISHED [1436, 3309]   ORACLE_VERIFIED [1436, 3309]
adaptive-eligible submissions: 0
bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6
```

## Reseed preflight — read the finding before running anything

**"Bulk reseed" is not re-running the oracle.** `reseed_questions` calls an LLM
(Gemini) to regenerate question content and writes it back.

| question | answer |
|---|---|
| Candidates | **1,141** — questions whose `content` contains `PLACEHOLDER_MARKER` ("In this problem, you are tasked with solving the"), i.e. filler statements that were never real problems |
| Excluded | everything else. **q3309 and q1436 are not candidates**; 0 PUBLISHED, 0 ORACLE_VERIFIED, 0 with references in the set |
| Tables written | `groups_question` only |
| Columns written | `content`, `boilerplate_code`, `hidden_test_cases` (`execution_contract_version` deliberately excluded) |
| New hidden tests | **1,140 of 1,141** currently have empty suites and would receive LLM-generated ones, tagged `source: LLM_UNVERIFIED` |
| Existing suites | 1 question has cases already; reseed leaves them untouched ("regeneration requires the oracle pipeline") |
| Learner submissions affected | **0** — reseed never touches `CodeSubmission`; `adaptive_eligible` is frozen at submission time (proven by `test_trust_boundary`) |
| Reversible? | **No.** 0 of the 1,141 have a pre-image |
| Audit | none — no batch, no `RemediationAction` |
| Roles | **no role can run it.** It writes three columns; `remediate_rw` holds `content`, `boilerplate_rw` holds `boilerplate_code`, `hidden_test_rw` holds `hidden_test_cases`, and the separation is deliberate |
| Alias | none — it uses the default connection, which here is the read-only census role, so the writes would fail at the server |
| Dry-run | exists (`--dry-run`), plus `--limit`, `--topic`, `--retry-failed` |
| Overlap with the 772 PEP-585 starters | **0** |
| Projected fingerprint | not computable in advance — the content is LLM-generated, so the post-state is not deterministic |

**It predates P2.7 entirely and uses none of its machinery**: no `--alias`, no
pre-image capture, no batch, no `RemediationAction`, no digest handshake, no
production identity gate.

So bulk reseed as it stands would write LLM-generated hidden test cases —
grading truth — for 1,140 questions, with no rollback, no audit trail and no
role that is allowed to do it. That is exactly the class of write this milestone
was built to prevent.

The good news is that the safety property already holds by accident of the
selector: nothing verified, published, referenced or submitted-against is in
the candidate set.

## What the reseed needs before it can run

1. An `--alias` and a role. Either a new least-privilege `learnlm_reseed_rw`
   holding `UPDATE (content, boilerplate_code, hidden_test_cases)` — which
   deliberately unions three separated authorities and needs explicit review —
   or split the command so each column is written by its existing owner.
2. Pre-image capture for all 1,141 into a frozen batch, so it is reversible.
3. A `RESEED` action class (migration, like 0045/0046/0047) so it is audited.
4. A digest handshake and a production identity gate.
5. A real dry-run that reports projected digests per question, not just
   "would process N".
6. A decision on whether LLM-generated hidden tests may be written at all
   before an oracle exists — the current design tags them
   `LLM_UNVERIFIED`, which the trust pipeline then has to treat as legacy.
