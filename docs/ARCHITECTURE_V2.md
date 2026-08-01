# SparkLM v2 — Architecture Specification (FROZEN)

**Status:** Frozen. This document is the single source of truth. Any implementation that
deviates from it requires an explicit amendment recorded in §16 (Amendment Log).
**Supersedes:** ad-hoc decisions in code comments and conversation.
**Relationship to v1:** v1 is the current deployed codebase. v2 = v1 + the Phase A
engineering mandates (§2.4) + the staged scaling architecture (§15) + the research
instrumentation layer (§6.4). v2 is an evolution, not a rewrite.

---

## 1. Complete System Architecture

```
[Cloudflare CDN/WAF]
      │
      ├── SPA (static, Vercel-class host)
      │
      ▼
[API Gateway / ALB]
      │
      ▼
[API tier — stateless ASGI (Daphne), N instances]
      ├── REST /api/v1/*          (DRF)
      ├── WebSocket /ws/*         (Channels: chat, CRDT, grading results)
      │
      ├──> [Redis A]  cache · throttles · channel layer · learner-state projections
      ├──> [Redis B]  Celery broker (Phase C+; absent in Phase A/B)
      │
      ▼
[Worker tier — Celery (Phase C+)]
      ├── queue: grading   (per-user routing key)
      ├── queue: content   (LLM batch: reseed, backfill, isomorphs)
      └── queue: periodic  (decay sweeps, retraining, projections rebuild)
      │
      ├──> [Judge0]  RapidAPI (Phase A/B) → self-hosted isolated fleet (Phase D)
      ├──> [LLM services]  Groq (content), Gemini (embeddings/study tools)
      │
      ▼
[PgBouncer] → [PostgreSQL primary (+ read replica Phase D)]
[S3-compatible object storage]  media · model artifacts · archived submission code
[Observability]  Sentry · Prometheus/Grafana · structured JSON logs
```

**Why this shape.** A *modulith*: one deployable backend with strictly layered internals.
**Rejected:** microservices (operational cost unjustified below ~10 engineer-team scale;
the seams are preserved so extraction stays possible); serverless functions (cold starts
poison WebSockets and long grading jobs; per-invocation pricing punishes judge fan-out).
**Tradeoff accepted:** one blast radius for backend deploys, in exchange for one CI/CD
pipeline, one test suite, and one mental model.

---

## 2. Backend Architecture

### 2.1 Layering (strict, enforced in review)

```
api/ (views, serializers, permissions, throttles)   ← HTTP only, no business logic
        ▼
services/ (GradingService, ProgressionService,
           RecommendationService, ContentService)    ← orchestration + transactions
        ▼
engines/ (elo, hlr, router, gdcp→belief, xai, coach) ← pure domain logic, no HTTP,
                                                        DB access only via passed objects
        ▼
models/ (ORM) + selectors (read queries)             ← persistence
```

Rules: views never mutate learner state directly; engines never import DRF; services own
transactions and locking; selectors own reusable read queries. External I/O (Judge0, LLMs)
is injected into services as callables — this is what keeps the test suite offline.

### 2.2 Concurrency model
All learner-state mutation happens inside a single `transaction.atomic` block with
`select_for_update` on the profile and mastery rows, lock order: profile → mastery(topic-id
ascending). Network calls (Judge0, coach webhook) are forbidden inside that block. From
Phase C, per-user Celery routing keys serialize each user's grading jobs, making the locks
a belt-and-suspenders measure rather than the primary mechanism.

### 2.3 Async grading contract (Phase C+)
`POST /api/v1/submissions` → validate → persist `PENDING` → enqueue → `202 {submission_id}`.
Completion is pushed on the user's WebSocket channel; `GET /api/v1/submissions/{id}` is the
polling fallback. `SYNC_GRADING=true` preserves the v1 synchronous behavior for development
and tests. **Rejected:** long-polling only (wastes the Channels investment); fire-and-forget
(loses the result contract).

### 2.4 Phase A engineering mandates (prerequisites for everything else)
1. Traffic-Cop statistic fix (variance → runs-test streakiness; model artifact versioned to v2).
2. Row-locking as per §2.2.
3. Hidden test cases removed from client payloads; `sample_case` exposed instead.
4. Telemetry endpoint restricted to staff.
5. Composite index catalog (§4.4) applied.
6. Service-layer extraction from `CodeSubmitView` (pure move, tests unchanged).

---

## 3. Frontend Architecture

- React 18 + TypeScript + Vite SPA. Feature-folder layout (§9), shadcn/ui + Tailwind.
- **Server state:** TanStack Query becomes the standard for all new data fetching
  (deduplication, caching, retry). Existing raw `fetch` calls migrate opportunistically.
  **Rejected:** Redux (no complex client-only state exists); RSC/Next (SSR buys nothing
  for an authenticated app; hosting simplicity wins).
- **Auth:** access token in memory, refresh via httpOnly cookie (§13.2). localStorage
  token storage is deprecated and removed in Phase B.
- All origins from `VITE_API_URL` / `VITE_WS_URL`. No literal hosts — CI greps for
  regressions.
- Async grading UX: submission states `queued → running(case i/n) → verdict`, driven by
  WebSocket with polling fallback.
- Testing: Vitest + React Testing Library smoke tests on the four critical flows
  (auth, get-problem, submit, learning path). **Tradeoff:** no exhaustive component
  coverage — accepted; backend contract tests carry correctness.

---

## 4. Database Schema

### 4.1 Retained v1 entities (unchanged semantics)
`CodingPortal`, `Topic`, `TopicPrerequisite` (acyclicity enforced in `clean()`),
`Question`, `UserCodingProfile`, `CodeSubmission`, `UserTopicMastery`, `AgenticCoachLog`,
`RecommendationLog`, `Badge`/`UserBadge`, plus community/study models.

### 4.2 v2 additions
| Table | Purpose |
|---|---|
| `QuestionRating` (or fields on Question: `rating`, `rating_deviation`, `attempt_count`) | Two-sided Elo item calibration; CSV difficulty demoted to prior |
| `RecommendationLog.propensity` (+ `policy_version`) | Off-policy evaluation of routing decisions |
| `CalibrationPrediction` (user, submission, predicted_pass, ts) | Predict-then-submit metacognition signal |
| `TopicBelief` (user, topic, mastery_posterior, updated_at) | Belief layer replacing GDCP accuracy mutation; observed accuracy becomes immutable |
| `IsomorphLink` (source_question, generated_question, validated) | Transfer-testing review items |
| `LearnerStateProjection` (Redis-first, Postgres snapshot) | O(1) rolling telemetry (ring buffer of last 20 outcomes, streak stats) |

### 4.3 Data lifecycle
`CodeSubmission` partitioned by month **from Phase A** (cheap now, painful later);
`code` bodies archived to object storage after 90 days; anonymized research export
job (consented users only) as a first-class artifact.

### 4.4 Index catalog (authoritative)
`CodeSubmission(user, -submitted_at)` · `CodeSubmission(user, question, -submitted_at)` ·
`CodeSubmission(user, status)` · `Question(topic, base_difficulty)` ·
`RecommendationLog(user, problem_id, -created_at)`.
New tables index on their query shape at creation time; JSONFields get application-level
schema validation on write (never trust-and-crash at read).

**Rejected:** NoSQL/document store (relational integrity — DAG acyclicity, uniqueness,
transactions — is load-bearing here); separate analytics DB before Phase D (replica +
partitioning suffice).

---

## 5. ML Architecture

- **Model registry:** every artifact versioned (`routing_classifier_v2.pkl`,
  `gcn_dsa@YYYYMMDD.onnx`) in object storage; loaders pin versions; no silent overwrites.
- **Feature contracts:** each model's input vector documented alongside the artifact;
  changing a feature bumps the artifact version (the v1 variance defect is the cautionary
  precedent).
- **Serving:** web tier stays torch-free. Heuristic XAI is the default; SHAP/GCN path
  remains behind `ENABLE_SHAP_XAI`, honestly labeled in docs as attribution over a
  synthetic-trained model until retrained on production data. ONNX is the only inference
  format allowed in serving processes; torch lives in workers/offline only.
- **Evaluation gate:** no model ships without an offline evaluation artifact
  (holdout AUC/Brier for predictors; IPS/DR estimate for policies) checked into
  `docs/evals/`. This rule is absolute.
- **Retraining:** `retrain_ai` runs in the `periodic` queue, writes new versions,
  never hot-swaps without the evaluation gate.

---

## 6. Adaptive Learning Architecture

### 6.1 Decision pipeline (per recommendation request)
```
LearnerStateProjection ──► Router (Thompson bandit over {DAG, PRACTICE};
                                   heuristic prior; propensity logged)
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
        Curriculum engine           Practice engine
        (DAG unlock on              (success-band 0.70–0.80,
         EFFECTIVE mastery)          softmax sampling, interleave prior)
                 └────────────┬────────────┘
                              ▼
                    XAI layer (radar + weak-topic recommendation)
                              ▼
                    RecommendationLog (prediction + propensity)
```

### 6.2 Frozen definitions
- **Mastery (skill):** posterior belief per topic (BKT-lite), initialized from
  accuracy≥0.8 ∧ reviews≥3 rule; observed accuracy is never mutated (GDCP becomes soft
  evidence into `TopicBelief`, bounded per day).
- **Effective mastery = skill × predicted retention(t).** Curriculum gates on effective
  mastery; topics may re-lock. This is v2's flagship behavioral change.
- **Ratings:** two-sided Elo, uncertainty-scaled K, response time via within-item
  normalization (Math-Garden style), never absolute-millisecond multipliers.
- **Memory:** SM-2 rule retained as cold-start scheduler; DAS3H fit replaces constants
  once ≥50k review events exist.
- **Streakiness:** Wald–Wolfowitz runs-test z over the outcome window; n<5 → 0.

### 6.3 Coach
Tier ladder unchanged (3/5/7). New in v2: hint tier consumed discounts SM-2 quality
(HDQ); hint events become learner-model features.

### 6.4 Research instrumentation layer (build order fixed)
1. Predict-then-submit calibration (§4.2 table) — first, cheapest, highest value/effort.
2. Misconception fingerprints: per-submission failure bitmask + error class persisted;
   offline clustering job; per-student misconception counters.
3. Isomorph retention testing via the content pipeline.
Ghost-learner counterfactual explanations remain notebook-stage until uncertainty
communication is designed; they do not enter the product UI in v2.

---

## 7. API Contracts

- Versioned under `/api/v1/`. v1 (unversioned) paths remain as aliases until the SPA
  migrates, then 301-deprecated. OpenAPI schema generated (drf-spectacular) and committed;
  the schema file is the contract of record.
- **Envelope:** success = resource JSON; error = `{ "error": { "code", "message", "details?" } }`
  with stable machine `code`s. No HTML errors, ever.
- **Key contracts (shapes, not code):**
  - `POST /api/v1/submissions` → 202 `{submission_id, status:"queued"}` (Phase C+; 200 sync in Phase A/B).
  - `GET /api/v1/submissions/{id}` → status/result document identical to the WebSocket payload.
  - `GET /api/v1/next-problem?topic=` → problem card + `sample_case` + XAI payload
    (`shap_values`, `dominant_factor`, `success_probability`, `weak_topics`,
    `recommendation`). **Never** hidden test cases.
  - `POST /api/v1/predictions` → predict-then-submit vote `{submission_ref, will_pass}`.
  - `GET /api/v1/mastery-map?subject=` → nodes with `mastered/unlocked/depth/accuracy_pct/effective_mastery`.
- Throttle scopes frozen: `judge0 10/min`, `recommend 30/min`, `user 120/min`, `anon 15/min`,
  `auth 5/min` on token obtain, `auth-refresh 30/min` on token refresh (amended — see §16).
  Anonymous throttle identity is the FIRST `X-Forwarded-For` hop, via
  `common.throttling` — **not** `NUM_PROXIES`. On Render the last hop is a
  rotating internal load balancer, which made every IP-keyed throttle inert in
  production. The accepted cost is that a client rotating its own XFF entry can
  mint fresh buckets; a spoofable limit strictly dominates the absent one it
  replaced. See §16 and docs/DEPLOYMENT.md.
- camelCase in JSON is deprecated; snake_case for all new fields (`hiddenTestCases` is the
  cautionary fossil).

---

## 8. Event Flow (canonical sequences)

**Submission (Phase C+):** SPA submits → API validates + persists PENDING + enqueues
(propensity-tagged if recommendation-driven) → worker grades per test case against Judge0
→ worker opens locked transaction: rating update, belief update, HLR/HDQ, flywheel closeout
→ commit → coach check (outside txn) → result event on channel → SPA renders verdict →
projection updated (same worker, single-writer).

**Recommendation:** SPA requests → projection read (O(1)) → router samples arm (logs
propensity) → engine selects item (success-band) → XAI composed → RecommendationLog row →
response. No LLM calls on this path (serve-time generation moves to the content queue with
a daily budget — flagged as a cost-DoS vector otherwise).

**Review scheduling:** nightly `periodic` job computes retention per (user, topic);
effective mastery updated; due topics ranked; isomorph selected (or generation enqueued)
→ Review Queue surface.

**Content pipeline:** reseed/backfill/isomorph jobs run in `content` queue; every LLM
output passes schema validation; invalid output is never persisted; daily-quota exhaustion
stops the job cleanly and resumes on the marker system.

---

## 9. Folder Structure (target; strangler-pattern migration)

```
backend/
  LearnLM/                 # settings, asgi, urls (unchanged)
  groups/                  # v1 legacy module — FROZEN: bugfixes only
  coding/                  # v2 home: api/ services/ selectors/ models/
  learning/                # engines: rating.py router.py memory.py belief.py xai.py
  content/                 # LLM pipeline: generators, validators, commands
  common/                  # auth, throttles, error envelope, observability
  docs/evals/              # model evaluation artifacts (gate, §5)
studysphere-ai-11/src/
  features/<feature>/      # components + hooks + api per feature
  lib/                     # api client (single fetch wrapper), query client
  components/ui/           # design system only
```
Rule: new code lands in v2 modules; `groups` shrinks by extraction, never grows.
**Rejected:** big-bang restructure (guaranteed regression against 33 passing tests for
zero user value).

---

## 10. Technology Stack (frozen versions = major-pinned)

Python 3.12 · Django 5.2 · DRF 3.17 · Channels 4 / Daphne 4 · Celery 5 (Phase C+) ·
PostgreSQL 15+ + pgvector · Redis 7 · Judge0 CE · React 18 · TypeScript 5 · Vite 5 ·
TanStack Query 5 · Tailwind + shadcn/ui · pytest + pytest-django · Vitest + RTL ·
scikit-learn / ONNX Runtime (serving) / torch (offline only) · Sentry · Prometheus/Grafana ·
Terraform (Phase C+) · GitHub Actions.
**Rejected:** FastAPI rewrite (ORM/admin/Channels ecosystem loss for marginal latency);
Next.js (§3); Kafka (Redis/Celery covers 100× current scale); Kubernetes before Phase D
(ECS/Fargate is one student's ops budget).

## 11. Design Principles (binding)

1. **Decorations never break the core** — XAI, coach, gamification fail to fallbacks, never to 500s.
2. **Observed data is immutable** — models write beliefs, never observations.
3. **Every adaptive decision is logged with its propensity** — the platform is its own experiment.
4. **No unvalidated LLM output is ever persisted.**
5. **Single writer per learner state** (worker keying; locks as backstop).
6. **Torch-free serving; versioned artifacts; evaluation-gated shipping.**
7. **Quota-aware, idempotent, resumable batch jobs** — always.
8. **Environment-driven config; identical code across environments.**
9. **Feature flags for behavior changes touching learners** (`SYNC_GRADING`, `ENABLE_SHAP_XAI`, router policy version).
10. **Honest naming** — no component claims more than it does (the "HLR" lesson).

## 12. Coding Standards

Backend: ruff (lint+format), type hints required on services/engines, docstrings state
*why*, no `print` (structured logging only), migrations reviewed for lock impact,
constants centralized (thresholds named, no magic numbers in engines), tests accompany
every behavior change (regression tests reference the incident they encode).
Frontend: ESLint + prettier, no `any` in new code, single API client wrapper (no raw fetch
in components), feature folders own their types.
Git: imperative commit subjects, body explains why; one concern per commit; CI green
before merge; no attribution trailers.

## 13. Security Standards

1. Untrusted code executes only in Judge0 sandboxes; the judge fleet (Phase D) runs on
   isolated, egress-blocked, credential-free nodes.
2. **Auth v2:** short-lived access token in memory + httpOnly SameSite refresh cookie;
   rotation on refresh; server-side revocation list. (Deprecates localStorage tokens.)
3. Secrets only via environment/secret manager; production refuses dev SECRET_KEY;
   quarterly key rotation for third-party APIs.
4. Authorization: staff-only analytics endpoints; object-level checks in services for
   any cross-user resource; students never receive hidden test data.
5. Dependency scanning (pip-audit, npm audit) in CI; Dependabot on.
6. Privacy: telemetry disclosure in-product; consented, anonymized research exports;
   editor process signals (fluency) are opt-out and content-free (counts/timings only).
7. Rate limits at edge (Cloudflare) + DRF scopes; `auth` scope throttles credential stuffing.

## 14. Testing Strategy

Pyramid: **engine unit tests** (pure, property-based where invariants exist: rating
symmetry, half-life bounds, belief monotonicity) → **service tests** (transactional
behavior, locking via threaded double-submit) → **API contract tests** (the current
33-test suite's tier; all third parties mocked at the injection seams) → **4 E2E smoke
flows** (frontend, Vitest+RTL) → **k6 load baseline** re-run before each deployment phase.
Rules: the suite runs fully offline; CI gates on pytest against a real Postgres service;
coverage floor 80% on `learning/` and `services/`; every logged model prediction gets a
calibration check in `docs/evals/` per release.

## 15. Deployment Strategy (staged; triggers are metrics, not dates)

| Phase | Trigger | Topology |
|---|---|---|
| **A (now)** | — | Single ASGI instance (Render-class) + Neon Postgres + Upstash Redis + Vercel SPA; RapidAPI Judge0; sync grading |
| **B** | first real users | Same + Sentry + backups automation + httpOnly auth + CDN |
| **C** | thread saturation / >2k subs/day | Celery workers + async grading contract + Terraformed ECS |
| **D** | RapidAPI invoice > self-host cost / >20k subs/day | Self-hosted judge fleet, read replica, partitopped reads, learner-state projection authoritative |

Blue-green deploys from Phase C; migrations backward-compatible for one release
(expand-migrate-contract); nightly pg backups with restore rehearsal each phase;
SLOs: API p99 < 300ms, grading p95 < 15s (async), judge queue depth alarmed.

---

## 16. Amendment Log

| Date | Section | Change | Rationale |
|---|---|---|---|
| 2026-07 | — | Initial freeze | — |
| 2026-07 (M1 review) | §7 | Token refresh split onto its own `auth-refresh` scope (30/min); login keeps `auth` (10/min) | Refresh presents an existing token, not guessable credentials; a shared bucket let routine refresh traffic behind NATed IPs starve sign-ins |
| 2026-07 (M1 review) | §7, §13 | `NUM_PROXIES` (default 1, env `DRF_NUM_PROXIES`) added to DRF config | Without it, DRF keys anonymous throttles on the raw client-supplied X-Forwarded-For header — spoofable rotation bypassed every IP-keyed throttle behind the target proxy topology |
| 2026-07 (M2-1) | §4.3 | `CodeSubmission` DB-level primary key is `(id, submitted_at)`; `id` uniqueness guaranteed by its sequence | Postgres requires the partition key inside every unique constraint of a partitioned table. Nothing currently references `CodeSubmission` by FK; any future referencing table (e.g. `CalibrationPrediction`, §4.2) must either include `submitted_at` in the reference or reference `id` with application-level integrity |
| 2026-07 (M2-2) | §4.3 | `id` is sequence-backed (`DEFAULT nextval`) rather than an IDENTITY column | PostgreSQL < 17 forbids IDENTITY columns on partitioned tables; insert semantics are identical |
| 2026-07 (M2-3) | §4.4 | Single-column `user_id` FK index not carried onto the partitioned table; single-column `question_id` index retained | `user_id` equality scans are served by the leading column of `subm_user_ts_idx`/`subm_user_status_idx`; no catalog composite leads with `question_id` |
| 2026-07 (M2-4) | §4.3 | A `DEFAULT` partition backstops the monthly ranges; `ensure_submission_partitions` (common app) maintains the horizon and relocates strays | An insert must never fail for lack of a partition; the maintenance command is idempotent and self-healing after downtime |
| 2026-08 (M3-A) | §7 | Passwords hashed with Argon2id at pinned parameters (`t=2, m=19456 KiB, p=1`) in `common/hashers.py`; PBKDF2 retained for verification | Django's stock Argon2 defaults (100 MiB, p=8) would OOM the 512 MB instance at four concurrent logins, and an OOM costs every later visitor a ~93 s cold start. Legacy accounts migrate transparently on next sign-in; rollback is a REORDER, never a removal |
| 2026-08 (M3-B, SEC-B1) | §7, §13 | Anonymous throttle identity moved OFF `NUM_PROXIES` onto the first `X-Forwarded-For` hop (`common/throttling.py`) | `NUM_PROXIES=1` keys on the LAST hop, which on Render is a rotating internal load balancer: 12 sequential requests landed in three buckets and no limit was ever reached, so throttling was measured completely inert in production. `REST_FRAMEWORK["NUM_PROXIES"]` is deliberately retained — these classes bypass it, and it is the correct setting again behind a stable proxy. Accepted cost: a client rotating its own XFF entry evades the limit (test-pinned) |
| 2026-08 (M3-B) | §7 | `anon` 30→15/min, `auth` 10→5/min | Rate limits were set as security controls with no reference to capacity. The old ceiling permitted anon(30)+auth(10)=40 req/min from one IP, and 40 concurrent auth requests was measured returning 32×502 with a ~60 s outage — the limit authorised the outage. New ceiling 20/min is the highest burst measured to complete with zero failures. Per-IP, so a NATed campus shares a bucket: raise only together with real capacity |
| 2026-08 (M3, R1) | §13 | `DATABASES` uses a psycopg connection pool (`OPTIONS['pool']`, `CONN_MAX_AGE=0`, `CONN_HEALTH_CHECKS=True`) | `CONN_MAX_AGE` never applied under Daphne: `ASGIHandler` opens a `ThreadSensitiveContext` per request, and `django.db.connections` is thread-critical, so every request opened a fresh connection to Neon (~7.2 round trips). Measured 20 requests → 21 TCP sockets before, 25 requests → 3 after; production p50 `/healthz` 699→392 ms. The pool lives in `DatabaseWrapper._connection_pools`, a class attribute, so it outlives request threads. Health checks are mandatory: Neon drops idle connections and the free tier auto-suspends, and without validation the pool serves dead connections as 5xx |
