# SparkLM — Final Canonical Roadmap

**Created:** 2026-09-01 · **Deadline:** 2026-09-03 (2 days) · **HEAD:** `097f67d`

**This document supersedes every other roadmap for scheduling purposes.** It is
a reconciliation, not a new plan: it takes what the repository can be shown to
contain and lays the remainder against the remaining time.

---

## 0. Why this exists — the roadmap estate was inconsistent

Seven planning documents existed, and they did not agree with each other or
with the code.

| Document | Lines | Scheme | State |
|---|---|---|---|
| `EXECUTION_ROADMAP.md` | 622 | M0–M11, 32 phases, ~93.5 ed | **LOCKED 2026-08-06.** Every milestone still reads `Status: NOT STARTED` while git shows M1–M6 merged and P2.7 driven through 23 phases. **Trust its scope, not its checkboxes.** |
| `MASTER_ENGINEERING_ROADMAP.md` | 471 | E0–E10 epics, week-phased | Claims to supersede the others. Written before the entire P2.7 content-trust arc. Epic E5 says "Measure the GNN or retire it" — the GNN was retired in M1/P1.1. |
| `ROADMAP_V2.md` | 40 | M1–M16 | Superseded, incompatible numbering |
| `ROADMAP_SEPT_3_2026.md` | 272 | 6-day countdown | **Closest to reality.** Written 2026-08-29; its "2 of 2,926 trusted" and "agent not connected" are now stale (6 trusted; agent wired). |
| `NEXT_TASK.md` | 73 | pointer | **Stale.** Dated 2026-08-09, points at "P1.2 — Collapse coding surfaces", which never ran and was overtaken by the content-trust work. |
| `EXECUTION_CHECKLIST.md` | 252 | tracker | Tracks `EXECUTION_ROADMAP` phases |
| `TECHNICAL_DEBT.md` / `FUTURE_SCOPE.md` | 268 / 104 | backlog | Still useful as backlog |

**None of them contains the work that actually dominated the last month:**
P2.7 phases 11–23, the KT track (P2.10–P2.13), the agent track (P2.11a, P2.14,
P2.23), or the P2.18–P2.22 trust transitions.

**Rule going forward:** this file schedules. `EXECUTION_ROADMAP.md` remains the
scope reference for anything not listed here. `TECHNICAL_DEBT.md` remains the
backlog.

---

## 1. The single number that governs everything

> **6 of 2,926 questions are trusted. 1,788 are reachable by a learner.
> 0 of 44 submissions are adaptive-eligible.**

Every downstream ambition — Random Forest routing, knowledge tracing on real
learners, Glicko-2 arming, agent-driven personalisation — is starved by the
same upstream fact: **there is almost no trustworthy evidence.** Not because
the pipeline is broken, but because it is gated on human specification
authoring and only 5 specifications exist.

**Therefore the critical path is content, and it always has been.** Model work
cannot substitute for it, and this roadmap does not pretend otherwise.

---

## 2. NOW — before 2026-09-03

Two days. Only work that is finishable and verifiable belongs here.

| # | Task | Why now | Done when |
|---|---|---|---|
| N1 | **Canonical roadmap + architecture docs** (this file, `TRAFFIC_COP_RESEARCH.md`, `SPARKLM_INTERVIEW_ARCHITECTURE.md`) | The estate is contradictory; a reader cannot tell what is true | Committed, cross-checked against code |
| N2 | **Retire the stale roadmap pointers** — mark `NEXT_TASK.md`, `ROADMAP_V2.md`, `EXECUTION_ROADMAP.md` checkboxes as superseded by this file | A stale "read this first" is worse than none | Header banner on each |
| N3 | **Fix `ai_services.py` withdrawn Groq model** | Three call sites 404 today. One-line-per-site fix, already diagnosed | Calls resolve; test asserts the id comes from `PROVIDER_MODELS` |
| N4 | **Trust-aware serving decision** (design, not necessarily ship) | 1,788 reachable vs 6 trusted is the biggest honesty gap in the product | Either a UI label for untrusted questions, or a written decision to accept |
| N5 | **Rotate the Gemini key** *(operator action, not code)* | Still reachable in 9 commits of public history | Confirmed rotated at Google |

**Explicitly NOT in NOW:** training the Random Forest, arming Glicko-2, any KT
model change, bulk reseed. See §7 for why.

---

## 3. NEXT — the first weeks after the deadline

| # | Task | Value | Depends on |
|---|---|---|---|
| X1 | **Operator writes specifications, batch by batch** | Unblocks literally everything else | Human time only |
| X2 | Run the reseed pipeline on each approved batch | Trusted count rises | X1 |
| X3 | `rotate_suite` — write inputs and answers in one transaction | Closes the real (if tiny) rotation window | — |
| ~~X4~~ | ~~Structured Traffic Cop observability~~ | **DONE** (M2 P2.24). One JSON event per decision, with latency. Answers routing questions **from now on** — there is no historical structured data to answer them retrospectively | — |
| X5 | Retire the stale root `package.json` | Vercel build fragility | — |
| X6 | `question_approve` cp1252 fix | Blocks trust transitions on Windows | — |
| X7 | Frontend test coverage (ratio 0.15 vs backend 1.16) | Weakest test surface | — |

---

## 4. RESEARCH — evidence-gated, not calendar-gated

| # | Track | Gate that must open first |
|---|---|---|
| R1 | **Random Forest routing** | **≥100 labelled `RecommendationLog` rows.** Today: 35, and **none can be produced** — labels are only written for `adaptive_eligible` submissions, of which there are 0. Gate is doubly closed: it needs trusted questions first. |
| R2 | **KT on SparkLM learners** | ≥1 adaptive-eligible submission, then volume. Today: 0. |
| R3 | **SAKT** | Nothing external. This is the architecture the P2.10a literature review actually argues for at this data scale, and it is declared `NOT IMPLEMENTED`. |
| R4 | **AKT with Rasch embeddings seeded from Glicko** | R3 first. The Rasch factorisation is structurally what two-sided Glicko already computes. |
| R5 | **Glicko-2 arming** | Its own written gate: ≥30 days **and** ≥10,000 shadow decisions (vs 44 total submissions), plus calibration no worse than baseline. |
| R6 | **Agent vs Traffic Cop offline comparison** | Needs labelled outcomes — same gate as R1. |

---

## 5. FUTURE — deliberately not scheduled

TA-GTKT-P (prerequisite-aware KT) · Thompson-sampling router · Celery worker
tier · self-hosted judge fleet · read replica · external uptime monitor ·
migration off `google-generativeai` · Elo-matched duels · post-solve LLM code
review · router prediction-accuracy dashboard.

---

## 6. Component status — the canonical table

| Component | State | Evidence |
|---|---|---|
| **Content trust pipeline** | ✅ PRODUCTION | 6 questions end-to-end, 238 oracle executions, 8 approvals |
| **Bulk reseed** | 🔴 BLOCKED | 24/24 candidates skipped, no specifications |
| **Traffic Cop — heuristic** | ✅ PRODUCTION | `decide_route`, `learning/router.py:76` |
| **Traffic Cop — Random Forest** | ❌ NOT ACTIVE | No artifact; `models_data/` empty; `clf is None` verified live |
| **Agent Orchestrator** | 🟡 EXPERIMENTAL | Wired, tested, `AGENT_ORCHESTRATOR_ENABLED` default `false` |
| **Elo** | ✅ PRODUCTION | `services.py:683` — the live rating |
| **Glicko-2** | 🟡 SHADOW | `services.py:729` writes; nothing reads |
| **BKT / DKT / Transformer / TA-GTKT** | 🔬 RESEARCH | Trained on ASSISTments; no model in the request path |
| **Curriculum DAG** | ✅ PRODUCTION | 22 topics, 19 edges; `CURRICULUM_GATE_ENFORCE` off |
| **Trusted candidates** | ✅ / 🔴 split | Agent path filters on trust (6). Legacy path does not (1,788) |
| **Deployment** | ✅ PRODUCTION | Vercel + Render + Neon + Upstash; no deploy gate, no staging |
| **Testing** | ✅ | 3,432 passing |
| **Documentation** | ✅ as of this file | README, PROJECT_STATUS, ARCHITECTURE, this roadmap |

---

## 7. The three things this roadmap refuses to do

**1. Train the Random Forest on 35 rows.** The gate is 100 and it stays at 100.
The 35 existing labels were produced *before* the trust gate landed on
2026-08-11 — i.e. from verdicts against unverified answer keys, exactly the
data the gate now excludes. Training on them would teach the router from the
same corrupted signal the whole content-trust programme exists to eliminate.

**2. Arm Glicko-2 because it is implemented.** It has a written five-condition
gate it has not met. "It exists" is not evidence it is better.

**3. Start SAKT/AKT/TA-GTKT-P before the deadline.** They are real and
justified — and they are research, gated on nothing external, which means they
can wait. Starting them now would trade a finishable deliverable for an
unfinishable one.

---

## 8. Definition of done for 2026-09-03

**Must be true:**
- One canonical roadmap; contradictory pointers marked superseded
- Architecture documented and matching the code, with PRODUCTION / EXPERIMENTAL
  / SHADOW / RESEARCH / FUTURE labels applied consistently
- Traffic Cop audited against the literature, with a written decision
- Tests green
- No secrets, no grading truth in the repository

**Must NOT be claimed:**
- That the Random Forest is live, or has metrics
- That Glicko-2 replaced Elo
- That TA-GTKT is production-trained on SparkLM learners
- That the bank is trusted, or that bulk reseed is complete
- That the agent is authoritative production routing
