# SparkLM — Project Status

**Last updated:** 2026-08-06 · **Updated by:** CTO-level repository audit
**Read this first in every new session.** Then `NEXT_TASK.md`.

---

## What SparkLM is

An adaptive coding-practice and study platform. A learner picks a topic, gets a
problem matched to their Elo, submits code that is graded against hidden test
cases on Judge0, and their skill estimate, spaced-repetition schedule and
curriculum position update from the result. Around that sit study groups,
shared materials, an AI doubt-solver over uploaded documents, quizzes and chat.

## Deployment topology (live)

| Tier | Where | Plan | Notes |
|---|---|---|---|
| SPA | Vercel — `spark-lm-3y3e.vercel.app` | — | Vite/React 18/TS 5 |
| API | Render — `sparklm-api.onrender.com` | **free** | 512 MB, 0.1 shared vCPU, Daphne/ASGI, single instance |
| DB | Neon Postgres 15 + pgvector | free | US East (Ohio); API pinned to Virginia to match |
| Cache/Channels | Upstash Redis | free | `REDIS_URL`; falls back to in-memory if unset |
| Judge | Judge0 via RapidAPI | free tier | synchronous, one HTTP call **per test case** |
| LLM | Groq (primary) → NVIDIA NIM (daily-quota fallback); Gemini for vision/RAG | free | |

Config of record: `render.yaml`, `studysphere-ai-11/vercel.json`.

## Size

| Area | Files | Lines | Test ratio |
|---|---|---|---|
| Backend source | 98 | 12,854 | **0.79** (10,150 test lines, 498 test fns) |
| Frontend app | 45 | 10,782 | **0.15** (1,655 test lines, 93 `it()` blocks) |
| shadcn UI kit | 49 | 3,954 | vendored, not ours |
| Migrations | 36 | — | |

354 tracked files. `.venv` correctly ignored.

## Health summary

**Strong.** Row-locked learner-state transactions with a documented lock order
and no network calls inside them. Default-deny DRF permissions with an
enforcing authorization matrix test. Client-IP-aware throttling derived from
measured capacity. Monthly partitioning of `CodeSubmission`. Argon2id hashing.
A strict CSP. Architectural guard tests that fail the build on regression.

**Weak.** A large share of the *visible product* is inert: the dashboard's
headline numbers are hardcoded, notifications have no producer, badges are
never awarded, streaks are never incremented. Every list in the app is
silently truncated to 3 items. Two completed milestone phases (object storage,
auth v2) are built, tested, merged — and **not switched on in production**.
The entire torch-dependent ML surface cannot execute on the web tier.

## The single most important fact for a new session

> **Engineering quality and product reality have diverged.** The codebase is
> better than the product. Work that adds new subsystems is almost always
> lower value right now than work that makes existing subsystems actually
> produce data. Check `TECHNICAL_DEBT.md` before proposing anything new.

## Current branch state

| Branch | State |
|---|---|
| `main` | production; **at `40b4ba5`** — P1.1 merged 2026-08-09 (PR #7), **fully verified** |
| `m6-phase1a-fix-broken-pages` | merged; retained for history |
| `preserve/settings-email-2xx-test` | parked work (`d7b0e20`), awaiting a keep-or-drop decision |
| `p1-1-followup-xai-labels` | SHAP-label fix (`4f89c3e`), **closed PR #8** — deferred to M2 / Product Truth |

Phase 1a is **merged, pushed, CI-green and deployed.**

## Verified test state (2026-08-09)

- Frontend: **104 passing** (7 files) — verified locally on the merged tree.
- Typecheck (`tsconfig.app.json`): clean. Production build: succeeds.
- Backend: **verified by CI** — "Backend Tests (pytest + Postgres)" passed on
  run `31300049244`. Docker was down locally, so CI is the authority.
- Guard mutation check: 3/3 detectors killed their mutants.

## Milestone numbering warning

Two incompatible schemes coexist. `docs/ROADMAP_V2.md` numbers M1–M16.
Git branches use `m4-security-sprint`, `m5-phase1..4`, `m6-phase1a`. They do
**not** correspond. `MASTER_ENGINEERING_ROADMAP.md` supersedes both; use its
epic IDs (E1–E9) from now on.
