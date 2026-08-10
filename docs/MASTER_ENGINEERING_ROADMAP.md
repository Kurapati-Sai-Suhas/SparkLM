# SparkLM — Master Engineering Roadmap

**Status:** source of record. Supersedes `ROADMAP_V2.md` (whose ordering was
driven by a job interview dated 2026-07-23 and has expired) and reconciles the
two conflicting milestone numbering schemes.
**Created:** 2026-08-06 from a full repository audit. **Living document.**

Companions: `PROJECT_STATUS.md` · `NEXT_TASK.md` · `TECHNICAL_DEBT.md` ·
`FUTURE_SCOPE.md` · `DAILY_PROGRESS.md`

---

## 1. Executive summary

SparkLM's engineering is markedly better than its product.

The backend shows real discipline: row-locked learner-state transactions with a
documented lock order and no network I/O inside them; default-deny DRF
permissions backed by an enforcing authorization-matrix test; throttles derived
from load measurements rather than guesswork; monthly partitioning of the
submission table; Argon2id; a strict CSP; guard tests that fail the build when
architecture regresses. The test:source ratio is 0.79.

The product a user actually sees does not reflect that. The dashboard's three
headline numbers are hardcoded constants. Notifications, badges and streaks
have tables, endpoints and UI but **no code path that ever writes to them**.
Every list is silently truncated to three items. Two completed milestone
phases — durable object storage and httpOnly refresh cookies — are merged,
tested, and switched off in production, so uploads still vanish on deploy and
refresh tokens still sit in localStorage. The torch-dependent ML stack (GNN,
SHAP, visual-search embeddings) is not installed on the web tier and cannot
execute at all.

**The strategic call for the next 12–18 months is therefore to stop adding
subsystems and start making the existing ones produce data.** The roadmap below
front-loads that, then addresses the one true scaling ceiling (synchronous
per-test-case Judge0 grading), and only then invests in the question bank that
every ambitious product goal depends on.

**What I am deprecating**, on evidence: the belief layer, propensity logging,
Thompson-sampling router and misconception fingerprints (`ROADMAP_V2.md`
M11–M14). These are research-grade personalisation features on a product whose
dashboard cannot yet count a quiz. They stay in `FUTURE_SCOPE.md` and return
only when there is traffic to justify them.

---

## 2. Architecture (as built)

```
Vercel SPA (React 18 / TS 5 / Vite 7)
  │  HTTPS  — single axios client, src/services/api.js
  │  WSS    — 3 consumers: chat, notifications, code-collab
  ▼
Render free tier · Daphne ASGI · ONE thread-sensitive sync worker
  ├── DRF: default-deny perms, IP-aware throttles, admission control (limit 12)
  ├── common/  auth (JWT + token_version revocation), storage, dashboard, health
  ├── groups/  views · coding_views · services (Grading, Progression)
  │            hybrid_router (RoutingClassifier→sklearn, HierarchicalEngine→networkx)
  │            consumers (Channels)
  └── learning/ memory.py, router.py
  ▼
Neon Postgres 15 + pgvector          Upstash Redis (cache + channel layer)
  CodeSubmission partitioned monthly   ⚠ silent in-memory fallback
  ▼
Judge0 (RapidAPI) — synchronous, ONE CALL PER TEST CASE  ← scaling ceiling
Groq → NVIDIA NIM fallback · Gemini (vision, long-context "RAG")
```

**Load-bearing constraint:** the ASGI handler runs sync views on a single
thread-sensitive worker, measured in `settings.py:432-437`. Requests queue, they
do not parallelise. Serialised login cost ~0.63 s ⇒ ~1.6 logins/s ceiling.
40 concurrent requests from one IP produced a ~60 s outage. Every throttle in
the system is a capacity control first and a security control second.

---

## 3. Current repository health

| Dimension | Grade | Basis |
|---|---|---|
| Backend correctness & concurrency | **A−** | documented lock order, race tests, farming guard under lock |
| Authorization & security | **A−** | default-deny + matrix test; auth v2 built but off |
| API design | **B−** | 40+ endpoints, no versioning, no OpenAPI schema, `PAGE_SIZE:3` |
| Data model | **C+** | strong on submissions; `Question` too thin; `Topic.name` uniqueness bug |
| Frontend architecture | **C+** | one API client now; React Query unused; no error boundary |
| Product completeness | **D** | dashboard hardcoded; notifications/badges/streaks inert |
| ML/AI | **C−** | production runs heuristics only; no eval harness (`docs/evals/` empty) |
| Testing | **B** | backend 0.79 ratio, frontend 0.15, no mutation/coverage gate in CI |
| Deployment | **C** | free tier, single instance, `migrate` in start chain, flags off |
| Observability | **C+** | Sentry DSN-gated, access log; no metrics, no tracing, no alerts |
| Documentation | **C** | 640 KB, ~400 KB interview prep; two conflicting roadmaps |

---

## 4. Epics

> **⚠ REORDERED 2026-08-06 (second review).** The original ordering put
> "activate what is built" first, on the assumption that SparkLM is a live
> product with users to protect. **Repository evidence contradicts that
> assumption:** three core pages were "broken in production for months and
> nothing reported it" (`FRONTEND_NETWORK_ACCESS.md:27`), and
> `RecommendationLog` — which gains a row on *every* problem request — held
> **177 rows** in total (`models.py:483`). SparkLM has effectively no external
> users. Where there are no users, deferred activation costs nothing, while a
> hollow product costs everything, because the product *is* the artifact being
> evaluated. Configuration work has been demoted accordingly. See §4a.

| ID | Epic | Why now | Effort |
|---|---|---|---|
| **E0** | Merge Phase 1a | Finished work, three pages fixed, zero cost | 0.5 ed |
| **E1** | Scope decision — cut before building | Breadth is now the primary liability | 4 ed |
| **E2** | Product truth | Every visible number is fabricated or empty | 14 ed |
| **E3** | Coding platform excellence | The differentiated core loop, and a latency problem users feel | 12 ed |
| **E4** | Question bank v2 | The substance of an "AI learning platform" | 25 ed |
| **E5** | Adaptive learning, honestly | Measure the GNN or retire it — either is a result | 12 ed |
| **E6** | AI quality | RAG isn't retrieval; generation has no evals | 12 ed |
| **E7** | Content seeding to 500 | A platform with no content is a demo | 15 ed |
| **E8** | Frontend consolidation & UX | One data layer, one coding surface, error boundaries | 12 ed |
| **E9** | Infrastructure activation | Config only; **hard gate before any public launch** | 2 ed |
| **E10** | Performance, observability, CI | Justified by measurement, not anticipation | 10 ed |

Total ≈ 118 engineer-days. Order optimizes for engineering and portfolio
value; §4a records what changed and why.

## 4a. What the reordering changed

| Item | Was | Now | Reason |
|---|---|---|---|
| Object storage activation | E1, first | E9 | Nothing of value is being lost; ~0 real uploads exist |
| Auth v2 activation | E1, first | E9 | Portfolio value is in the *code*, which already exists; flipping a flag adds none |
| Email config | E1, first | E9 | Only sender is one manual button (`settings_views.py:66`) |
| Question bank v2 | E6 | E4 | Everything downstream depends on item metadata; seeding first creates migration debt |
| Adaptive-learning honesty | E9, last | E5 | The GNN cannot run in production; measuring it is the highest-signal engineering result available |
| **Scope reduction** | *absent* | **E1** | 11 nav items, 3 coding surfaces, 3 AI surfaces and orphaned routes, built by one engineer |

---

## 5. Phased plan

### Phase 0 — Merge and activate (week 1) · E1
Phase 1a is complete on a branch and unmerged. Everything else is operator
action on already-shipped code.

| Task | Owner | Notes |
|---|---|---|
| Merge `m6-phase1a-fix-broken-pages` → `main` | eng | PR, squash-free |
| Provision object-storage bucket, set 5 vars, migrate media | **you** | D2 |
| Set `AUTH_V2_COOKIES=true` after confirming SPA build | **you** | D3, staged |
| Set `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | **you** | D32 |

**Ends with:** uploads survive deploys; refresh tokens leave localStorage;
email works. Zero new code.

### Phase 1 — Stop lying to users (weeks 2–5) · E2 + D1
The highest-value engineering in this document.

1. `PAGE_SIZE` fix + guard test (D1) — **do this first, it is one line and
   affects every screen**
2. `notify()` service with real producers; paginate notifications (D4)
3. Badge award rules inside `apply_submission`; `seed_badges` (D5)
4. Streak update under the existing profile lock (D6)
5. Real dashboard aggregates; delete tiles that cannot be computed (D7, D8)
6. `ErrorBoundary` at the route level (D18)

### Phase 2 — Throughput (weeks 6–9) · E3
1. Judge0 batch submissions: N calls → 1 (D9)
2. Partial-failure tolerance in `GradingService` (D9)
3. `EXISTS` anti-join in `NextProblemView` (D11)
4. Remove in-request LLM generation (D12)
5. Submit idempotency keys (D27)
6. Then evaluate async grading — only if measurement still shows a ceiling

### Phase 3 — Foundations (weeks 10–12) · E4 + E8
1. `Topic` uniqueness → `('portal','name')` (D14)
2. `RecommendationLog` retention/partitioning (D13)
3. Loud Redis failure + Redis service in CI (D15)
4. WebSocket rate limiting and payload caps (D16)
5. CI: coverage gate, frontend lint, mutation testing on changed files (D33)
6. API versioning (`/api/v1/`) + OpenAPI schema

### Phase 4 — Frontend consolidation (weeks 13–16) · E5
1. N3 Phase 1b: migrate the 6 allowlisted files; empty the allowlist
2. Decide React Query — adopt or remove (D17)
3. Collapse three coding surfaces to one (D19)
4. Tests for the four largest untested pages (D22)

### Phase 5 — Question bank v2 (weeks 17–22) · E6
See §7. Schema, authoring, validation, review workflow, versioning.

### Phase 6 — Seeding to 1,000 (weeks 23–28) · E7
See §8.

### Phase 7 — Adaptive learning, honestly (weeks 29–32) · E9
1. Build the offline eval harness `docs/evals/` promises and never delivered
2. Measure GNN routing against the Elo baseline on `RecommendationLog`
3. **Then** fund a worker tier or retire the torch stack (D20)
4. Two-sided Elo + item calibration — the one ML item with clear value

---

## 6. Dashboard roadmap

**Current:** four tiles, three of them constants; a leaderboard; a badge strip
that is always empty; a staff-only MLOps panel running three unbounded
`COUNT(*)`s.

**Target — every widget backed by a real query:**

| Widget | Source | Phase |
|---|---|---|
| Problems solved, accuracy, current Elo | `CodeSubmission`, `UserCodingProfile` | 1 |
| Streak (real) | `UserCodingProfile.current_streak` after D6 | 1 |
| Quizzes passed | `QuizResult` | 1 |
| Submission heatmap (GitHub-style, 12 months) | partitioned `CodeSubmission` | 2 |
| Topic mastery radar | `UserTopicMastery.accuracy` | 2 |
| Due-for-review count | `/api/review/queue/` (exists) | 1 |
| Elo trajectory line chart | `CodeSubmission` + rating history *(needs a new table)* | 3 |
| Weakest-topics panel with drills | `_build_recommendation` (exists, unsurfaced) | 1 |
| Daily goal / weekly report | new | 3 |
| Career-readiness score | needs company/difficulty tags → depends on E6 | 5 |

**Principle:** a tile that cannot be computed does not ship. Deleting the
"Study Hours" tile is a better outcome than showing a zero.

---

## 7. Question bank roadmap (E6)

**Current schema — 7 fields** (`groups/models.py:327`): `topic`, `title`,
`content`, `base_difficulty`, `boilerplate_code`, `hidden_test_cases`,
`hidden_wrapper_code`. Difficulty labels are derived from Elo thresholds at
`coding_views.py:472-474`. There are no tags, companies, hints, editorials,
constraints, public test cases, reference solutions, complexity annotations,
prerequisites, learning objectives, quality scores or versions.

Content quality is already a known problem in the code: `_servable_questions()`
(`coding_views.py:54-67`) quarantines placeholder rows **and** roughly 1,100
CSV-imported rows that have descriptions but zero test cases.

### Target model

```
Question           slug, title, statement_md, difficulty (enum + calibrated Elo),
                   status(draft/review/approved/retired), version, quality_score,
                   bloom_level, time_limit_ms, memory_limit_kb
QuestionTag        M2M — topics, patterns, companies
QuestionExample    ordered; input, output, explanation      (PUBLIC)
TestCase           input, expected, is_public, weight, group (PUBLIC + HIDDEN)
Hint               ordered, progressive reveal
Editorial          approach_md, complexity_time, complexity_space, code per language
ReferenceSolution  language, code, verified_at              (grading oracle)
Boilerplate        language, stub                            (replaces JSONField)
QuestionRelation   prerequisite / similar / follow-up
QuestionReview     reviewer, checklist results, verdict, notes
QuestionVersion    immutable snapshots; submissions pin a version
```

Two structural decisions worth making explicitly:

- **`hidden_test_cases` as a `JSONField` has no schema enforcement** — noted in
  the code itself at `coding_views.py:604`. Promote test cases to rows.
- **Reference solutions are the validation oracle.** A question is not
  approvable until every reference solution passes every test case on Judge0.
  This is what makes automated validation possible at all.

### Workflow

`draft → automated validation → human review → approved → (retired)`

Automated gates: statement non-empty and not a placeholder; ≥2 public examples;
≥5 test cases with ≥1 edge case; a reference solution per offered language that
passes all cases; boilerplate compiles; no duplicate slug; complexity recorded.

---

## 8. Seeding roadmap (E7)

Milestones are quality gates, not row counts. A milestone is not met until
every question at that tier passes the full validation checklist.

| Tier | Scope | Gate |
|---|---|---|
| **100** | Curated core across 10 topics, 3 languages | 100% reference-solution-verified; human-reviewed; used to calibrate difficulty |
| **250** | +Hints and editorials on all 250 | Editorial coverage 100%; tag taxonomy frozen |
| **500** | +Company tags; prerequisite graph populated | Difficulty calibrated from real submissions, not Elo guesses |
| **1000** | Full curriculum coverage; every topic ≥30 questions | Automated daily seeder + validator running in CI; quality score ≥0.8 median |
| **3000** | Long tail, contest-grade | Requires a content pipeline and probably paid authoring — revisit as a business decision, not an engineering one |

**Sequencing note:** do not seed past 100 until E6 lands. Seeding into the
current 7-field schema creates 1,000 rows that must all be migrated later.

---

## 9. Master checklist

Grouped Epic → Phase → Feature → Task. `[ ]` open, `[x]` done.

### E1 — Activate what is built
- [ ] **Phase 0: merge and switch on**
  - [ ] Merge `m6-phase1a-fix-broken-pages` into `main`
  - [ ] Deploy and verify `/schedule`, `/notifications`, `/settings` live
  - [ ] Provision object-storage bucket *(operator)*
  - [ ] Set `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` *(operator)*
  - [ ] Run `manage.py migrate_media_to_object_storage`
  - [ ] Verify an upload survives a redeploy
  - [ ] Confirm deployed SPA carries the Phase 4 auth client
  - [ ] Set `AUTH_V2_COOKIES=true`; verify refresh + logout; keep rollback ready
  - [ ] Set `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` *(operator)*

### E2 — Truth in the UI
- [ ] **Pagination**
  - [ ] Raise `PAGE_SIZE`; set explicit per-view page sizes
  - [ ] Guard test: no list view relies on the global default
  - [ ] Frontend: follow `next` or request explicit page sizes
- [ ] **Notifications**
  - [ ] `notify()` service (persist + push in one place)
  - [ ] Producers: submission milestones, quiz assignment, friend request, group invite
  - [ ] Paginate `NotificationView`; add ordering and a cap
  - [ ] Reconcile the WebSocket path with the persisted row
- [ ] **Badges**
  - [ ] `seed_badges` command with an initial catalogue
  - [ ] Award rules inside `ProgressionService.apply_submission`
  - [ ] Idempotency (`unique_together` already present)
- [ ] **Streaks**
  - [ ] Update `current_streak` / `highest_streak` / `last_active_date` under the profile lock
  - [ ] Timezone-correct day boundaries
- [ ] **Dashboard**
  - [ ] Real `quizzes_taken` from `QuizResult`
  - [ ] Real achievement points from `UserBadge`
  - [ ] Decide Study Hours: implement tracking or delete the tile
  - [ ] Delete the false comment at `Dashboard.tsx:28`
  - [ ] Surface `_build_recommendation` weak-topics on the dashboard
- [ ] **Resilience**
  - [ ] Route-level `ErrorBoundary` with a recovery action

### E3 — Grading throughput
- [ ] Judge0 batch endpoint: N calls → 1
- [ ] Tolerate partial failure; do not discard passed cases
- [ ] `EXISTS` anti-join in `NextProblemView`
- [ ] Remove in-request `generate_test_cases`
- [ ] Submit idempotency key
- [ ] Re-measure; decide on async grading with data

### E4 — Data model
- [ ] `Topic` `unique_together = ('portal','name')` + migration
- [ ] Decide and enforce whether `Topic.portal` may be null
- [ ] Fix generic-harness method selection (D10) + backfill
- [ ] `RecommendationLog` retention + partitioning
- [ ] Resolve `CodeSubmission.question` nullability

### E5 — Frontend
- [ ] N3 Phase 1b: migrate all 6 allowlisted files; allowlist reaches zero
- [ ] Decide React Query: adopt or remove
- [ ] Collapse `/code`, `/coding-hub`, `/coding-portal` to one surface
- [ ] Tests for Dashboard, AIQuiz, GroupDetail, AdaptiveCodingPortal

### E6 — Question bank v2
- [ ] Schema design review (this document, §7)
- [ ] Migrations: `TestCase`, `Hint`, `Editorial`, `ReferenceSolution`, `QuestionTag`, `QuestionExample`, `Boilerplate`, `QuestionRelation`, `QuestionVersion`, `QuestionReview`
- [ ] Backfill from existing `JSONField`s
- [ ] Validation pipeline (reference solutions as oracle)
- [ ] Review/approval workflow + admin surfaces
- [ ] Versioning; submissions pin a version
- [ ] Quality score

### E7 — Seeding
- [ ] 100 curated, 100% verified
- [ ] 250 with hints + editorials
- [ ] 500 with company tags + prerequisite graph
- [ ] 1000 with automated daily seeder in CI
- [ ] Revisit 3000 as a business decision

### E8 — Observability & CI
- [ ] Loud Redis failure; Redis service in CI
- [ ] WebSocket rate limits + payload caps
- [ ] Coverage gate; frontend lint; mutation testing on changed files
- [ ] `/api/v1/` versioning + OpenAPI schema
- [ ] Metrics (submissions/min, Judge0 latency, LLM cost) + alerts
- [ ] Move `migrate` out of `startCommand`

### E9 — Adaptive learning
- [ ] Build the offline eval harness (`docs/evals/` is empty)
- [ ] Measure GNN vs Elo on `RecommendationLog`
- [ ] Decide: worker tier or retire torch stack
- [ ] Two-sided Elo + item calibration

### Continuous
- [ ] Archive `10_INTERVIEW_HANDBOOK_PART_*` to `docs/archive/`
- [ ] Mark `ROADMAP_V2.md` superseded
- [ ] Replace `google-generativeai` (EOL) and `PyPDF2` (deprecated)
- [ ] Update `DAILY_PROGRESS.md` every session

---

## 10. Engineering workflow

Every feature, without exception:

```
Architecture Review → Implementation Plan → Task Breakdown → Implementation
→ Testing → Mutation Testing → Self Review → Engineering Review → Fixes
→ Re-review → Branch → Commit(s) → Pull Request → Merge → Deploy
→ Post-Deployment Review
```

**Why each stage is independent:**

- **Architecture review before planning.** The most expensive defects in this
  repository are architectural, not local: `PAGE_SIZE:3` truncating every list,
  a guard that watched only one of two transports, three coding surfaces. None
  would be caught by code review of a diff.
- **Plan before tasks, tasks before code.** A plan states intent; the task
  breakdown is where scope is negotiated. Merging them hides scope creep.
- **Mutation testing separate from testing.** This repository has repeatedly
  shipped tests that could not fail for their stated reason — most recently
  three assertions in the network guard, all of which survived having their
  detector disabled. A passing suite is not evidence; a suite that fails when
  you break the thing is.
- **Self review before engineering review.** Reviewers should spend attention
  on judgment, not on things the author could have caught.
- **Re-review after fixes.** Fixes introduce defects at roughly the rate
  original code does.
- **Separate branch per phase.** Rollback granularity equals branch
  granularity. If auth v2 and object storage share a branch, reverting one
  reverts both.
- **Separate commits.** `git bisect` is only as precise as the commits. The
  CORS outage was diagnosed in minutes because the offending header was one
  isolated commit.
- **Separate PR.** Review quality falls off a cliff past a few hundred lines.
- **Post-deployment review.** The CORS outage passed every test and every
  review and still broke production, because the failure only existed in the
  browser–Render–Vercel triangle. Deployment is a test that no CI can run.

**Rollback:** phase = branch = PR = one revert. Feature flags for anything
touching auth or storage, defaulting off, with byte-identical behaviour when
off — as `AUTH_V2_COOKIES` already does.

**Production safety:** no schema change and code change in the same deploy for
destructive migrations; expand → migrate → contract.

---

## 11. Git workflow

- Branch naming `e{N}-{phase}-{slug}` (e.g. `e2-phase1-notifications`),
  replacing the drifted `m4/m5/m6` scheme.
- One phase per branch, one logical change per commit.
- **Stacked PRs must use merge commits, never squash or rebase** — rewriting
  SHAs orphans every child branch.
- Never commit secrets; `sync: false` in `render.yaml` is the pattern.
- CI green before merge; `main` is always deployable.
- Tag production deploys.

---

## 12. Context-reset strategy

Claude's context will reset. The first action of every new session is:

1. Read `PROJECT_STATUS.md` → where the project is.
2. Read `NEXT_TASK.md` → what to do next, with branch and commit strategy.
3. Read `TECHNICAL_DEBT.md` → what not to be surprised by.
4. Read this file's §9 checklist → what is done and what is open.
5. Restate: current phase · completed · remaining · blocked · next task ·
   estimate · branch name · commit strategy.
6. **Only then** implement.

At the end of every session, append to `DAILY_PROGRESS.md` and update
`NEXT_TASK.md`. A session that ends without updating these two files has
lost most of its value.
