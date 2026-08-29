# SparkLM — consolidated roadmap to 3 September 2026

**Created:** 2026-08-29 (audit + recovery phase).
**Status:** NEW. No prior roadmap in this repository mentions a September 3
deadline — verified by full-tree search for `September 3 / Sept 3 / Sep 3 /
2026-09-03`: **zero matches**.

**Supersedes for scheduling purposes:** nothing. `EXECUTION_ROADMAP.md`
(LOCKED 2026-08-06) remains the engineering authority for M0–M11 scope; this
document schedules the *remaining* work against a delivery date it never had.

---

## 0. Why this document exists, and what it is not

`docs/EXECUTION_ROADMAP.md` is a 622-line, 32-phase, ~93.5 engineer-day plan.
It is real and it is good, but it has two properties that make it unusable as
a countdown to Wednesday:

1. **Its status markers are stale.** Every milestone from M0 to M11 still
   reads `Status: NOT STARTED`, while git history shows M1–M6 branches merged
   and P2.7 driven through twenty-three phases. Trust its *scope*, not its
   checkboxes.
2. **It predates the work that now dominates the project.** P2.7 phases 11–23,
   the KT track (P2.10–P2.13) and the agent track (P2.11a) are not in it.

So this roadmap does not redesign anything. It takes what the repository can
be shown to contain, and lays the remainder against six calendar days.

**Today is Saturday 29 August 2026. Six days remain, inclusive of the 3rd.**

---

## 1. The one thing that decides whether the deadline is met

Everything else in this document is subordinate to a single number:

> **2 of 2,926 questions are PUBLISHED and ORACLE_VERIFIED.
> 0 of 44 learner submissions are adaptive-eligible.**

The adaptive loop is fully built and cannot be demonstrated end to end,
because a learner cannot be routed through a bank that has two trustworthy
items in it. Every other track — a strong KT result, a well-guarded agent
skeleton — is a component of a product that currently cannot show its
headline behaviour working.

**Therefore the critical path is content trust, not model quality.** The KT
and agent tracks are already at or near demo-sufficient; the bank is not.

---

## 2. What the deadline does and does not require

The brief is explicit that "bulk reseed architecture/proof complete" is a
different milestone from "entire bank processed", and that distinction is what
makes 3 September achievable at all.

| By 3 September | After 3 September |
| --- | --- |
| Pipeline proven end to end on a real slice | The remaining ~1,100 questions |
| Enough trusted questions to demo the adaptive loop | Full-bank trust |
| Resumable, ledgered bulk operation | Long-running bulk execution |
| A KT result that is honest and reproducible | SAKT / AKT / prerequisite KT |
| An agent that orchestrates real services | Agent in the learner-facing product |

Processing 1,136 questions is **not** on the critical path and must not be
allowed onto it.

---

## 3. Verified current state

### Track 1 — bulk reseed / content trust

The architecture is essentially complete. Every stage named in the brief has
a command behind it:

| Stage | Command | State |
| --- | --- | --- |
| Candidate census | `question_bank_census`, `reseed_contract_census` | ✅ |
| Specification | `reseed_generate` (+ spec files) | ✅ |
| Statement generation | `reseed_statement` | ✅ |
| Presentation / conformance | `validate_question_bank`, presentation gate | ✅ |
| Signature declaration | `declare_signature` | ✅ |
| Contract declaration | `reseed_contract` | ✅ |
| Hidden-test generation | `expand_hidden_tests` | ✅ |
| Quality gate | `quality_gate` | ✅ |
| Reference candidates | `reference_create` | ✅ |
| Reference review | `reference_review` | 🔄 awaiting a human |
| Oracle | `oracle_execute` | ✅ (70 executions recorded) |
| Approval | `question_approve` | ✅ |
| Promotion / demotion | `question_promote`, `question_demote` | ✅ |
| Publication | `question_status` | ✅ |
| Batch orchestration | `reseed_orchestrate` | 🔄 stages 1–2 only, by design |
| Pre-images / ledger / rollback | `preimage_capture/inspect/rollback` | ✅ |
| Controlled bulk slice | — | ⬜ never run |
| Full-bank reseed | — | ⬜ |

**The blocker is not code.** Four pilot questions sit at DRAFT / UNVERIFIED
with seven reference solutions authored, four of them LLM-generated and
unapproved. The next gate is a *semantic judgement about whether a reference
solution is correct*, which is a human's to make. I have consistently refused
to certify my own references, and that position has not changed: reference
and hidden-test agreement is same-author evidence, not verification.

### Track 2 — learner intelligence / KT

**Elo is live. Glicko-2 is not, but it is not idle either — and I previously
described this wrongly.**

The accurate state, verified in code today:

- `EloEngine.calculate_new_rating` runs inside `ProgressionService.apply_submission`
  (`services.py:683`). It produces the rating a learner sees and the routing
  target `coding_views._select_question` uses. **This is the live engine.**
- `shadow.record_submission_safely` is called on every eligible submission
  (`services.py:729`), after commit, inside its own savepoint, swallowing all
  failures. **Glicko-2 therefore accumulates real state in production** in
  `LearnerTopicSkill` / `QuestionSkill`.
- **No production decision reads that state.** The only readers are
  `shadow_report` (an offline comparison command) and `kt_features` (a
  declaration module). Deleting the shadow writes would change nothing a
  learner experiences.

> **Correction.** In P2.11 I reported that `shadow.apply_submission` "is
> called by nothing except `shadow_report`". That was wrong about the write
> path — it is reached from `services.py` on every eligible submission. The
> conclusion it supported (Glicko is not the live engine) survives; the
> reasoning did not. "Unarmed" means *nothing reads it*, not *nothing runs it*.

KT research state:

| Component | State |
| --- | --- |
| Dataset pipeline (`kt_dataset`) | ✅ canonical schema v2, ASSISTments 2009 |
| Leakage-safe preprocessing | ✅ audited twice, independently |
| BKT | ✅ trained |
| DKT | ✅ trained |
| Transformer baseline | ✅ trained, frozen, 3 seeds |
| Temporal features | ✅ trained |
| Gated fusion | ✅ trained |
| TA-GTKT | ✅ trained, 1 seed |
| SAKT / AKT | ⬜ declared, not implemented |
| Prerequisite-aware (TA-GTKT-P) | ⬜ declared, not implemented |
| Evaluation / ablation / reproducibility | ✅ |
| Checkpointing | ✅ |

### Track 3 — agentic AI

`groups/agent/` contains `orchestrator.py` and `tools.py`: six tools, bounded
tool calls, a candidate set the model cannot widen, `commit` stripped from
model payloads, narration-only transcripts. 26 tests.

**It is not connected to anything.** No URL, no view, no LLM provider. It is a
library with no caller. That is the single largest gap in the demo story
after content trust.

---

## 4. Day-by-day plan

Three tracks run in parallel. Reseed is the critical path; KT and agent work
must not wait on it.

### Saturday 30 August — unblock everything

| Track | Work | Deliverable |
| --- | --- | --- |
| Reseed | **Operator reviews the four pilot references** (human gate) | 4 decisions recorded |
| Reseed | Confirm owner DB credential is available for grants | go / no-go |
| KT | Commit the P2.13 work now sitting uncommitted | clean tree |
| KT | 2 further TA-GTKT seeds in background | 3-seed mean ± std |
| Agent | Wire the orchestrator to a real provider behind a flag | agent runs against an LLM |

**Dependency:** the reference review gates every remaining reseed day.
**Risk:** HIGH — if the operator does not review, Track 1 cannot advance and
the demo falls back to a two-question bank.

### Sunday 31 August — close the pilot loop

| Track | Work | Deliverable |
| --- | --- | --- |
| Reseed | Oracle → approve → promote → publish the pilot four | 6 trusted questions |
| Agent | One authenticated endpoint, flag-gated, agent behind it | agent reachable |
| KT | Error analysis write-up; ablation doc finalised | P2.13 doc complete |

**Dependency:** Saturday's review. **Risk:** MEDIUM — privilege blockers have
cost a day twice before; budget for one.

### Monday 1 September — the controlled bulk slice

| Track | Work | Deliverable |
| --- | --- | --- |
| Reseed | **First controlled slice: 20–25 questions** through stages 1–2, then contract, tests, gate | slice at SIGNATURE_WRITTEN+ |
| Agent | Observe→plan→act loop against real services end to end | recorded transcript |
| Integration | Adaptive loop exercised on the trusted set | working demo path |

**This is the milestone that proves "bulk reseed architecture complete".**
**Risk:** MEDIUM — Gemini free tier is 20 requests/day; a 25-question slice
may need two days or the Groq provider.

### Tuesday 2 September — integration and evidence

| Track | Work | Deliverable |
| --- | --- | --- |
| Reseed | Push the slice as far through oracle/approval as review allows | trusted count up |
| All | End-to-end adaptive loop: recommend → solve → grade → rate → recommend | demo script |
| Docs | Architecture diagram; results write-up; README refresh | reviewable artefacts |

### Wednesday 3 September — freeze

| Track | Work | Deliverable |
| --- | --- | --- |
| All | Full regression (3,355 tests) | green |
| All | Final secret audit | clean |
| All | Deploy verification, frontend + backend | live |
| All | Demo rehearsal / recording | recording |

**No new capability lands on the 3rd.** The day is verification and buffer.

---

## 5. Critical path

```
operator reference review        (HUMAN — blocks everything below)
        ↓
oracle → approve → promote → publish the pilot four
        ↓
controlled bulk slice (20–25 questions)
        ↓
enough trusted questions to route a learner
        ↓
end-to-end adaptive loop demo
```

**Parallel and unblocked:** all KT work, all agent work, all documentation,
deployment verification, the security audit.

**Postpone past 3 September without hesitation:** full-bank reseed, SAKT,
AKT, prerequisite-aware KT, TA-GTKT-P, Glicko-2 arming, the git-history
rewrite, and the q3309/q1436 rotation unless the exposure is judged urgent.

---

## 6. Blockers

| # | Blocker | Owner | Impact |
| --- | --- | --- | --- |
| 1 | Four pilot references need semantic review | **Operator** | Blocks all reseed progress |
| 2 | `neondb_owner` credential needed for grants/migrations | **Operator** | Has cost a day twice |
| 3 | Gemini free tier: 20 requests/day | Provider | Caps slice size |
| 4 | Agent has no LLM provider wired | Engineering | No agentic demo |
| 5 | q3309 / q1436 published with leaked answer keys | Operator decision | Security, not deadline |
| 6 | P2.13 work uncommitted (13 modified, 12 untracked) | Engineering | Loss risk |

---

## 7. What "done" means on 3 September

**Must be true:**
- Reseed pipeline proven end to end on a real slice, resumable and ledgered
- Enough trusted questions to demonstrate adaptive routing
- KT result reported honestly, reproducibly, with an ablation
- Agent orchestrating real services through bounded tool calls
- Deployed, tested, secret-clean

**Must NOT be claimed:**
- That the bank is trusted (it will not be)
- That Glicko-2 is the live engine (it is not)
- That TA-GTKT is a novel architecture (it is a named configuration)
- That any model is production-integrated (none is)
