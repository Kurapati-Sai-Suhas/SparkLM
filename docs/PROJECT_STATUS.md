# SparkLM — Project Status

**Last updated:** 2026-09-01 · **Updated by:** full repository re-audit
**Read this first in every new session.** Then `NEXT_TASK.md`.

> The previous revision of this file was dated 2026-08-06 and had fallen **70
> commits** behind: it pointed at `main` as `40b4ba5`, counted 354 tracked files
> against today's 730, and 36 migrations against today's 51. Everything below
> was re-read from the code, the production database or git history on the date
> above. A "read this first" document that is a month stale is worse than no
> document, because it is believed.

---

## What SparkLM is

An adaptive coding-practice and study platform. A learner picks a topic, gets a
problem matched to their Elo, submits code that is graded against hidden test
cases on Judge0, and their skill estimate, spaced-repetition schedule and
curriculum position update from the result. Around that sit study groups,
shared materials, an AI doubt-solver over uploaded documents, quizzes and chat.

> **⚠ Grading is trustworthy for 6 questions out of 2,926.** That is the single
> fact a new session needs. The P2.7 audit measured a *correct* Two Sum solution
> exiting 1 with empty stdout on an AI-reseeded question: `reseed_questions`
> wrote space-separated test data but never set `execution_contract_version`, so
> those questions stayed on the v1 harness, which parses JSON per line.
> Separately — and worse — expected outputs were produced by an LLM and had
> never been verified by executing a trusted reference.
>
> **Consequence for the adaptive engine:** `accepted`/`rejected`, Elo and
> mastery derived from unverified questions are **not trustworthy signals**.
> That is contained rather than fixed: `CodeSubmission.adaptive_eligible` is
> frozen `False` at write time for them, so they can never teach the learner
> model. Topic, prerequisites, attempts, timestamps and streaks are behavioural
> facts and remain sound. Routing repair (P2.9) is deliberately sequenced after
> the bank is trustworthy, so the router is never tuned on corrupted labels.

## The content-trust position, in numbers

| | |
|---|---|
| Questions in the bank | **2,926** |
| `PUBLISHED` **and** `ORACLE_VERIFIED` | **6** |
| Reachable by a learner today | **1,788** |
| Carrying the placeholder marker | 1,137 |
| With hidden tests | 1,789 |
| Learner submissions | 44 |
| Submissions that are `adaptive_eligible` | **0** |
| Oracle executions recorded | 238 |
| Question approvals | 8 |
| Reference solutions | 7 |
| Execution contracts in use | v1 × 2,923 · v3 × 3 |

**Serving is not gated on trust.** `_servable_questions()` filters on the
placeholder marker and on empty hidden-test lists — not on `status` or
`trust_state`. So 1,782 questions with unverified answer keys are shown to
learners today. Their verdicts are contained, but the learner is still told
they are wrong when they may be right. Closing this is content work, not code.

## Deployment topology (live)

| Tier | Where | Plan | Notes |
|---|---|---|---|
| SPA | Vercel — `spark-lm-3y3e.vercel.app` | — | Vite / React 18 / TS 5. **Root Directory must be `studysphere-ai-11`** — a dashboard setting, not in the repo |
| API | Render — `sparklm-api.onrender.com` | **free** | 512 MB, 0.1 shared vCPU, Daphne/ASGI, single instance, region Virginia to match Neon |
| DB | Neon **PostgreSQL 17.11** + pgvector | free | US East (Ohio) |
| Cache/Channels | Upstash Redis | free | `REDIS_URL`; falls back to in-memory if unset |
| Judge | Judge0 via RapidAPI | free tier | synchronous, one HTTP call **per test case**, 5 languages |
| LLM | Groq (primary) → NVIDIA NIM (quota fallback); Gemini for vision/RAG | free | Gemini free tier is 20 requests/day |

Config of record: `render.yaml`, `studysphere-ai-11/vercel.json`.
Four GitHub Actions workflows: CI, keepalive, nightly maintenance, nightly
read-only question-bank validation.

## Size

| Area | Files | Lines |
|---|---|---|
| Backend source (excl. tests, migrations) | 180 | 40,752 |
| Backend tests | 102 | 47,411 |
| Frontend `src` (ts/tsx) | 98 | — |
| Migrations | 51 | — |
| **Tracked files** | **730** | |

Backend test-to-source line ratio is **1.16** — there is more test code than
source code. Frontend coverage is far thinner and is a known gap.

## Verified test state (2026-09-01)

- **Backend: 3,400 passing** on the full tree (`pytest --ignore=scripts`).
- **3,188 passing** on the narrower scope CI runs (`pytest groups common`).
  Both numbers are correct; always quote the scope with the number.
- `--ignore=scripts` is required — `scripts/test_judge*.py` are ad-hoc probes
  that abort collection.
- Frontend: **161 tests across 9 files**, last verified at `d47296b`.
- CI pins **Node 24.x**. On Node 20 the Vitest forks worker cannot start and
  both test files error *before running*, which reads as a red build over tests
  that never executed.

## Health summary

**Strong.** Row-locked learner-state transactions with a documented lock order
and no network calls inside them. Default-deny DRF permissions with an
enforcing authorization-matrix test. Client-IP-aware throttling derived from
measured capacity. Monthly partitioning of `CodeSubmission`. Argon2id hashing.
A strict CSP. Architectural guard tests that fail the build on regression. Ten
column-scoped Postgres roles that have refused two real mistakes.

**Weak.** A large share of the *visible product* is inert: hardcoded dashboard
numbers, notifications with no producer, badges never awarded. Two completed
milestone phases (object storage, auth v2) are built, tested, merged — and not
switched on in production. CD has no deploy gate, no staging, no post-deploy
smoke test and no automated rollback.

**Open defects worth knowing before you touch anything:**

| | |
|---|---|
| `ai_services.py` hard-codes `llama-3.3-70b-versatile` | Withdrawn model; three call sites 404. The reseed path is unaffected — it reads `RESEED_GROQ_MODEL`. |
| Gemini API key in public git history | Present in 9 commits (`a50b513` → `371bd3f`). **Rotation at Google is unverified.** Removing the prep docs that pointed at it did not fix this. |
| Stale root `package.json` still tracked | If the Vercel Root Directory setting is ever lost, `vite build` exits 127 again. |
| Stored stdin cannot express an empty string | `execution_adapter` line 343 filters blank lines. Affects any question with an empty-input branch; the workaround is a single-line JSON envelope. |
| `question_approve` crashes on Windows | Renders `→` under cp1252. Work around with `PYTHONIOENCODING=utf-8`. |

## The single most important fact for a new session

> **Engineering quality and product reality have diverged.** The codebase is
> better than the product. The bottleneck is not code — every stage of the
> content-trust pipeline has a working command behind it, and 6 questions have
> been through all of them. The bottleneck is that **1,136 questions need an
> operator to write a specification**, and only 5 specifications exist in the
> whole repository. Work that adds new subsystems is almost always lower value
> than work that makes existing ones produce trustworthy data. Check
> `TECHNICAL_DEBT.md` before proposing anything new.

## Current branch state

| Branch | State |
|---|---|
| `main` | production; **at `d87dc2a`**, pushed. 22 of 26 local branches are merged into it. |
| `backup/interview-docs-2026-08-31` | local only, never pushed — holds the interview package removed from the public repo. |
| `backup/pre-push-p2.7-2026-08-24` | retained checkpoint. |
| `preserve/settings-email-2xx-test` | parked work, awaiting a keep-or-drop decision. |

Merged branches are retained rather than deleted, deliberately.

## Where the work actually is

Recent history, newest first — the shape of the last two weeks:

```
d87dc2a  README audited line by line against the code
724c6fa  README corrections; interview notes removed from the repo
b43bb35  P2.22  24 draft specifications for operator review
4557a13  P2.21  bulk slice selected, then BLOCKED — no specifications
61b1a4e  P2.20  both compromised suites rotated; 0 exposed inputs survive
17a798c  P2.19  pilot trust transition — the bank goes 2 trusted → 6
3bd3bb4  P2.18b quality gate PASS on all four pilot suites
7ff96d7  P2.18  promotion blocked — the gate had never run
6dc3335  P2.17  Oracle evidence complete for the four pilot questions
c4eae77  P2.14  agent on real services; pilot reference decision pack
adbfe07  P2.13  TA-GTKT ablation — the gate is not what helped
```

## Learner modelling — what is live

| Component | State |
|---|---|
| Elo | **LIVE** — the rating the UI shows and the selector matches on |
| Half-life / spaced repetition | **LIVE** |
| Traffic Cop router | **LIVE** |
| Curriculum DAG | **LIVE**, enforcement flag `CURRICULUM_GATE_ENFORCE` off |
| Explainability payload | **LIVE**, single heuristic path — SHAP/GCN deleted in M1/P1.1 |
| Glicko-2 | **SHADOW** — writes real state on every eligible submission; **nothing reads it** |
| Agent orchestrator | **LIVE behind `AGENT_ORCHESTRATOR_ENABLED`, default false** |
| BKT / DKT / Transformer / TA-GTKT | **RESEARCH** — trained on public ASSISTments, never on SparkLM data |
| SAKT / AKT | declared `NOT IMPLEMENTED` |

"Unarmed" means nothing *reads* Glicko-2, not that nothing *runs* it.

## Milestone numbering warning

Two incompatible schemes coexist. `docs/ROADMAP_V2.md` numbers M1–M16. Git
branches use `m4-security-sprint`, `m5-phase1..4`, `m6-phase1a`. They do **not**
correspond. `MASTER_ENGINEERING_ROADMAP.md` supersedes both; use its epic IDs
(E1–E9). Note also that `EXECUTION_ROADMAP.md` still reads `Status: NOT STARTED`
for every milestone M0–M11 while git shows M1–M6 merged and P2.7 driven through
twenty-three phases — **trust its scope, not its checkboxes.**
