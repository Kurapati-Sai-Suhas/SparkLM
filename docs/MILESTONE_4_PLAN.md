# SparkLM — Milestone 4 Implementation Plan (Approved)

**Status:** Approved after principal-engineer review. Supersedes the draft of 2026-08-02.
**Baseline:** commit `96e5c21`.
**Review verdict:** 🟠 **The draft was rejected and rebuilt.** Roughly 60% of the proposed scope
was over-engineered for the platform's actual data volume, and one item was architecturally in
the wrong place. What survives is smaller, cheaper, and defensible.

---

## Part I — Review of the Draft Plan

### §1. The measurement that invalidated Phase A

The draft proposed two-sided Elo, exploration, and a calibration study as the headline milestone.
I never checked whether the platform has the data to support any of it. It does not.

| Metric | Production, 2026-08-02 |
|---|---|
| Users | **19** |
| Questions | **2,926** |
| **Total submissions, all time** | **40** |
| Recommendation logs | 177 |
| …with a labelled outcome | **31** |
| Attempts on the most-attempted question | 22 |
| Attempts on the 5th most-attempted question | **1** |

**Submissions per question: 0.014.** Item-level Elo needs tens of attempts per item to converge.
At this volume essentially every question would carry `attempt_count = 0` permanently, and
`rating_deviation` would never shrink below its prior. The draft proposed building a calibration
machine with no fuel, and adding a shared-row write lock to the hot path to do it.

The calibration harness is worse: **31 labelled outcomes**. Bucketed into five probability bins
that is six points per bin. That is not a calibration study, it is noise with a chart.

**This is the same error the draft's own §0 corrected.** I read a specification and reasoned
about the system it described rather than the system that exists. Correcting the documentation
and then planning against the same imagined scale is the more embarrassing version of the
mistake, because I had just written the correction.

### §2. Over-engineering

| # | Item | Verdict |
|---|---|---|
| **O1** | **Two-sided Elo rating updates** | **Cut.** No data. Building the update math now means maintaining, testing and locking around a code path that cannot produce a signal for months or years. |
| **O2** | **Epsilon-greedy exploration** | **Cut.** Exploration costs the individual learner to benefit the population. With 19 users there is no population. Serving deliberately suboptimal problems to 19 people to calibrate 2,926 questions is a bad trade at any epsilon. |
| **O3** | **Calibration study** | **Cut.** 31 labelled outcomes. Revisit at ≥ 500. |
| **O4** | **Hybrid retrieval + cross-encoder reranking** | **Cut.** RAG scope is *one user-selected document* — perhaps 20–50 chunks. Retrieving k=20 and reranking to 3 from a 30-chunk corpus is corpus-scale technique applied to a document-scale problem. Exact search over the persisted index is correct and faster. |
| **O5** | **Reference-solution test-case verification** | **Cut.** The draft's own risk C-R5 admits most questions have no stored reference solution. A feature whose scope is "the questions that already have the thing we would use to verify them" is not a feature. |
| **O6** | **httpOnly refresh cookie migration** | **Deferred.** Largest change in the plan, touches cross-origin deployment and the SPA refresh flow. Once the CSP is placed correctly (§4), it addresses most of the compound risk at a fraction of the cost. |

**Cut or deferred: six of the draft's twelve substantive items.**

### §3. A claim in the draft that was overstated

The draft asserted propensity "cannot be retrofitted" and used that urgency to justify Phase A
priority. **That is only true for a stochastic policy.** The current router is deterministic
given its inputs, so every logged recommendation has propensity 1.0 by definition — and that is
reconstructible after the fact, provided you know which policy ran.

What is genuinely unreconstructible is **`policy_version`**. If the routing logic changes and
nothing recorded which version produced a historical recommendation, that mapping is gone. So
the urgent, cheap, irreversible item is a version string — not a float. The draft had the right
instinct attached to the wrong column.

### §4. Architecture issues

| # | Issue | Severity |
|---|---|---|
| **A1** | **The CSP was in the wrong place.** The draft put a Content-Security-Policy header in Django middleware. The XSS surface is the **React SPA, served by Vercel from a different origin**. A CSP on JSON API responses protects Django-rendered pages — essentially just the admin. `studysphere-ai-11/vercel.json` **already exists**, and that is where the header belongs. The draft's highest-value security item would have shipped and done almost nothing. | **High** |
| A2 | **Deferred aggregation adds to the debt it fixes.** The draft's answer to the shared-row lock was a new batch job — while simultaneously listing "decay jobs unscheduled" as critical debt. Adding a second unmonitored periodic job to fix a problem caused by wanting a first one is circular. Consolidate into one maintenance command with a liveness signal. | Medium |
| A3 | **Admission control limit derived from the wrong threshold.** The draft set it at 20, "the highest concurrency measured with zero failures." But 20 concurrent took **103 seconds**. On a serialised worker the *n*-th queued request waits *n×W*; the limit should be set by acceptable wait time, not by the failure boundary. At W ≈ 0.6 s and a 10-second tolerance, the limit is closer to **12**. | Medium |
| A4 | **Phase labels contradicted the recommended order.** The draft labelled phases A/B/C and then recommended executing B.1 → B.2 → A → B.3 → C. Renumber to match execution. | Low |

### §5. Scalability problems

- **M4 as drafted made throughput worse.** It added per-request work — item rating updates,
  propensity writes — to a backend whose defining constraint is that requests serialise, and
  delivered zero capacity improvement. There was no latency-regression gate.
- **The chunk table has no retention policy.** Uploads grow it without bound; nothing prunes
  chunks for deleted or replaced materials beyond the cascade.
- **Nothing in M4 touches the serialisation ceiling**, which is correct — it belongs to M5 with
  a paid instance — but the plan should say so as a *stated non-goal with a trigger*, not leave
  it implicit.

### §6. Security concerns

| # | Concern | Detail |
|---|---|---|
| S1 | CSP misplacement | See A1. The mitigation would have been theatre. |
| S2 | **Token revocation had no revocation mechanism** | The draft added a `token_version` column and a comparison, and never specified *what bumps it*. A revocation primitive with no trigger is dead code. Needs a logout-everywhere endpoint and an admin action. |
| S3 | **Chunk isolation enforced only by a test** | RAG chunks contain document text. A missed owner filter leaks another user's study materials. A test proves today's code is right; it does not prevent tomorrow's query. Enforce in a manager or via the material FK join, not by remembering. |
| S4 | Indirect prompt injection unaddressed | Uploaded documents flow into prompts. Low severity today because the blast radius is the attacker's own session — but the draft deferred agentic tooling for this reason and then never wrote down the precondition. |

### §7. Technical debt the plan would have created

- **Feature-flag sprawl.** The draft added five flags (`ADAPTIVE_USE_ITEM_RATING`,
  `RAG_USE_PERSISTED_INDEX`, `RAG_ENABLE_RERANK`, `RAG_ENABLE_HYBRID`, `ADMISSION_LIMIT`) on top
  of two existing (`CURRICULUM_GATE_ENFORCE`, `ENABLE_SHAP_XAI`). **Seven flags, no lifecycle, no
  owner, no expiry.** `CURRICULUM_GATE_ENFORCE` has already been off "until after the interview"
  since July.
- **"Keep the old path for one release."** Stated for the RAG index with no mechanism to ensure
  anyone removes it. That is how dead code becomes permanent.
- **Schema additions to `Question` that nothing would populate meaningfully** — three columns
  carrying their defaults indefinitely, which is exactly the `UserTopicMastery.elo_rating`
  failure the draft itself cites as a cautionary tale.

### §8. Future maintenance issues

- A batch job that silently stops is the failure mode already documented for `calculate_decay`.
  Any new periodic work needs a heartbeat, or M5 will list it as debt.
- No staging environment, yet the draft's Phase B success criteria required a production
  concurrency test. That repeats the pattern that caused the outage.
- **No frontend test runner appears anywhere in the draft**, despite `AdaptiveCodingPortal.tsx`
  carrying real domain logic (`templateFor`, `availableLanguages`, `SELF_CONTAINED_LANGUAGES`)
  with zero coverage.

### §9. What the draft got right

Retained without change: the shared-row lock analysis (the risk was real even though the feature
is cut), the debt register, deferring async grading to M5, the trigger-based framing inherited
from architecture §15, and rollback-by-configuration as the default strategy.

---

## Part II — Approved Milestone 4 Plan

**Theme: make the platform safe and operationally honest at the scale it actually has.**

M3 proved the platform is operationally sound. M4 closes the security and observability gaps that
are correct at *any* scale, fixes the things that are actively broken, and adds only the
instrumentation that is cheap and irreversible. It deliberately builds **nothing** that requires
data the platform does not have.

| Goal | Statement |
|---|---|
| G1 | The XSS/token compound risk is mitigated where the risk actually lives. |
| G2 | Overload degrades to a fast 503 within an acceptable wait, not a 502 after wasted work. |
| G3 | Tokens are revocable, and something can actually revoke them. |
| G4 | Time-dependent models run on a schedule, and a job that stops is visible. |
| G5 | Routing decisions record which policy produced them. |
| G6 | RAG stops re-chunking per request. |
| G7 | Frontend domain logic has tests. |
| G8 | Every feature flag has an owner and an expiry. |

**Hard gate across all phases:** no net latency regression. `/healthz`, `/api/token/` and
`/api/code/next/` p50 must stay within 10% of the M3 baseline (391 / 551 / 1221 ms).

---

## Phase A — Safety Closeout

**Duration estimate:** days, not weeks. Ship first.

### Objective
Close the security gaps that are correct regardless of scale, and make overload degrade safely.
Delivers **G1, G2, G3**.

### Files likely to change

| File | Change |
|---|---|
| `studysphere-ai-11/vercel.json` | **CSP header** — this is where it belongs (§4/A1). |
| `common/middleware.py` (new) | Admission-control semaphore. |
| `LearnLM/settings.py` | `ADMISSION_LIMIT`; `SIMPLE_JWT["SIGNING_KEY"]` from `JWT_SIGNING_KEY` with `SECRET_KEY` fallback. |
| `groups/models.py` + migration | `User.token_version` (integer, default 0). |
| `common/authentication.py` (new) | JWT class comparing `token_version` against the already-fetched user row. |
| `common/auth_views.py` | `POST /api/auth/logout-all/` — bumps `token_version`. |
| `groups/admin.py` | Admin action to revoke a user's sessions. |
| `render.yaml` | `JWT_SIGNING_KEY`, `ADMISSION_LIMIT`. |
| `.github/workflows/ci.yml` | `pip-audit`. |
| New tests | `common/test_admission_control.py`, `common/test_token_revocation.py`. |

### Dependencies
None. Every item is independent and independently shippable. CSP and `pip-audit` are hours.

### Architecture
**CSP at the edge.** `vercel.json` headers with `default-src 'self'`, no `unsafe-inline` for
scripts, `connect-src` limited to the API origin. Ship as `Content-Security-Policy-Report-Only`
first; enforce after a clean window.

**Admission control.** Bounded semaphore in middleware; above the limit return 503 with
`Retry-After`. **Limit = 12**, derived from acceptable wait rather than the failure boundary:
at W ≈ 0.6 s serialised, the 12th queued request waits ~7 s, inside a 10 s tolerance. 20 was the
*failure* threshold and produced 103-second responses. `/healthz` exempt — a throttled health
check lies under exactly the load where truth matters.

**Revocation with a trigger.** `token_version` as a JWT claim compared against the user row
`JWTAuthentication` already fetches — genuinely free. A missing claim is treated as valid so
tokens issued before the deploy keep working. `POST /api/auth/logout-all/` and an admin action
are what make it usable.

**Key split.** `JWT_SIGNING_KEY` with fallback to `SECRET_KEY`, so the change is non-breaking
until deliberately rotated.

### Risks
| # | Risk | Sev | Mitigation |
|---|---|---|---|
| A-R1 | CSP breaks the SPA | High | Report-Only first; enforce only after a clean 48 h. |
| A-R2 | Admission limit too low, rejects real users | Medium | 12 is derived, not guessed; log every rejection; `ADMISSION_LIMIT=0` disables. |
| A-R3 | `token_version` breaks existing tokens | Medium | Default 0; missing claim = valid. |
| A-R4 | `pip-audit` fails the build on an unfixable transitive advisory | Low | Allowlist with expiry, reviewed each milestone. |

### Testing strategy
- CSP header present with expected directives; Report-Only violations collected and reviewed.
- Admission control: concurrency above the limit returns 503 + `Retry-After`, `/healthz` exempt.
  **Mutation-verified** — raise the limit and assert the test fails.
- Revocation: bumping `token_version` invalidates within one request; pre-deploy tokens accepted;
  `logout-all` bumps it; admin action bumps it.
- Key split: JWT verifies under `JWT_SIGNING_KEY`; unset falls back cleanly.
- `pip-audit` demonstrated failing on a deliberately vulnerable pin.
- Production: the 13-check battery. **Concurrency test capped at 15 — do not repeat the 40-way
  burst.**

### Rollback strategy
1. CSP — remove the header from `vercel.json`; instant, no backend deploy.
2. Admission control — `ADMISSION_LIMIT=0`. Config only.
3. Revocation — stop issuing the claim; missing claims already valid.
4. Key split — unset `JWT_SIGNING_KEY`. **Note:** this is a forced logout for anyone holding a
   token signed with the split key. Document it.
5. `pip-audit` — remove the CI step.

### Success criteria
| # | Criterion |
|---|---|
| A-S1 | CSP enforced at Vercel; zero violations for 48 h in Report-Only before enforcing |
| A-S2 | 15 concurrent → 503 with `Retry-After`, zero 502s, `/healthz` still 200 |
| A-S3 | `logout-all` invalidates every token for that user within one request |
| A-S4 | JWT verifies under a distinct signing key; rotating it does not touch sessions |
| A-S5 | `pip-audit` in CI with a demonstrated failing build |
| A-S6 | 220+ tests green; production battery 13/13; **no p50 regression > 10%** |

---

## Phase B — Operational Truth

**Duration estimate:** 1–2 weeks.

### Objective
Make periodic work actually run and visibly fail; record which policy produced each
recommendation; accumulate item-level observations without building rating math; test the
frontend logic that has none. Delivers **G4, G5, G7**.

### Files likely to change

| File | Change |
|---|---|
| `common/management/commands/run_maintenance.py` (new) | **One** consolidated periodic job: decay sweep, spaced-repetition notifications, and a heartbeat write. |
| `.github/workflows/maintenance.yml` (new) | Daily schedule. |
| `common/health.py` | `/healthz` reports maintenance-heartbeat age (non-fatal). |
| `groups/models.py` + migration | `RecommendationLog.policy_version` (CharField). `Question.attempt_count`, `Question.correct_count` (integers, default 0) — **counters only, no rating**. |
| `groups/hybrid_router.py` | Emit `POLICY_VERSION` constant with the route decision. |
| `groups/coding_views.py` | Persist `policy_version`. |
| `groups/services.py` | Increment item counters with atomic `F()` — **no lock**. |
| `studysphere-ai-11/` | Vitest + React Testing Library; tests for `templateFor`, `availableLanguages`, `SELF_CONTAINED_LANGUAGES`, `extensionFor`. |
| `.github/workflows/ci.yml` | Frontend test job. |

### Dependencies
- Phase A's `pip-audit` job establishes the CI pattern the frontend job follows. Otherwise none.
- Counters must use `F()` expressions, **not** `select_for_update` — this is the whole point of
  the change (see Architecture).

### Architecture
**One maintenance job, with a heartbeat.** The draft's mistake was proposing a second periodic
job while listing unscheduled jobs as critical debt. `run_maintenance` does decay, notifications,
and writes a `last_run` timestamp to the cache and a durable row. `/healthz` reports the age of
that heartbeat. A job that silently stops becomes visible without new infrastructure.

**Item counters without item ratings.** `attempt_count` and `correct_count` incremented with
atomic `F()` expressions — no `select_for_update`, therefore **no shared-row lock and no change
to the lock-ordering contract**. This is the irreversible half: observations accumulate from
today, and when volume justifies it, item ratings can be computed from them in batch. The
reversible half — the Elo update math — is deferred with a trigger.

**`policy_version`, not propensity.** A string constant bumped on any routing change. Propensity
for the current deterministic policy is 1.0 by definition and reconstructible from the version
(§3), so the float waits until exploration lands.

**Frontend tests.** Vitest on the pure logic first — the template/language functions are pure and
trivially testable. Component tests only where behaviour is non-obvious.

### Risks
| # | Risk | Sev | Mitigation |
|---|---|---|---|
| B-R1 | `F()` counters drift on CASCADE delete | Medium | Same known issue as profile counters. Counters are **derivable** from `CodeSubmission`; ship a `rebuild_counters` command alongside and treat them as a cache. |
| B-R2 | Maintenance job doubles decay | Medium | `apply_time_decay` already tracks `last_decay_applied_at` for this. Verify under scheduling. |
| B-R3 | GitHub Actions cron throttling delays the job | Low | Decay is daily-granularity; measured 54–213 min gaps are tolerable. Heartbeat makes lateness visible. |
| B-R4 | Frontend test job slows CI | Low | Runs in parallel with the Python job. |
| B-R5 | Counters add write cost to the hot path | Low | Two `F()` increments in an existing transaction. Covered by the latency gate. |

### Testing strategy
- Maintenance: idempotent — running twice applies decay once; heartbeat advances; a failure in
  one sub-task does not prevent the others.
- Heartbeat: `/healthz` reports a stale heartbeat without returning 503 (advisory, matching the
  boot-probe pattern).
- `policy_version`: 100% of new rows carry it; changing the constant is visible in new rows only.
- Counters: increment on submission; `rebuild_counters` reproduces them exactly from
  `CodeSubmission`; **no new lock acquired** (assert lock/query shape, mutation-verified by
  adding a `select_for_update`).
- Frontend: `templateFor` returns the stub for a missing language; `availableLanguages` excludes
  templateless ones; C/C++ get self-contained skeletons.
- Regression: full suite; production battery; latency gate.

### Rollback strategy
1. Maintenance workflow — disable the workflow. Nothing else depends on it.
2. `policy_version` — additive nullable column; stop writing it.
3. Counters — `ITEM_COUNTERS_ENABLED=false` skips the increments. Columns stay; they are additive
   and hold accumulated data worth keeping.
4. Frontend tests — remove the CI job.
5. **Do not reverse the migration.** Additive columns are tolerated by old code, and dropping
   them discards accumulated observations.

### Success criteria
| # | Criterion |
|---|---|
| B-S1 | Maintenance runs daily; heartbeat age visible on `/healthz`; a deliberately failed run is detectable |
| B-S2 | 100% of new `RecommendationLog` rows carry `policy_version` |
| B-S3 | Item counters increment correctly and `rebuild_counters` reproduces them exactly |
| B-S4 | **No new lock** in `apply_submission` — lock-shape test green, mutation-verified |
| B-S5 | Frontend test job in CI; the four template/language functions covered |
| B-S6 | 220+ tests green; **no p50 regression > 10%** |

---

## Phase C — RAG Index & Flag Hygiene

**Duration estimate:** ~1 week.

### Objective
Stop re-extracting and re-chunking documents on every question, and impose a lifecycle on feature
flags before they become permanent. Delivers **G6, G8**.

### Files likely to change

| File | Change |
|---|---|
| `groups/models.py` + migration | `DocumentChunk` — FK to `StudyMaterial`, `chunk_index`, `text`, `embedding` (`VectorField(512)`). |
| `groups/managers.py` (new) | Owner-scoped default manager for chunks (§6/S3). |
| `groups/views.py` | `RAGDoubtView` queries the index; `MaterialViewSet` chunks on upload and deletes chunks on replace. |
| `groups/ai_services.py` | Chunk-and-embed at upload. |
| `groups/management/commands/reindex_materials.py` (new) | Resumable backfill. |
| `docs/FEATURE_FLAGS.md` (new) | Flag register: owner, purpose, added date, **expiry**. |
| `LearnLM/settings.py` | Remove `ENABLE_SHAP_XAI` **or** give it an expiry; resolve `CURRICULUM_GATE_ENFORCE`. |

### Dependencies
- Backfill consumes Gemini embedding quota — resumable and batched, may run over several days.
- Independent of Phases A and B.

### Architecture
**Persist chunks; nothing more.** Extract, chunk (500/50, unchanged), embed, store with an owner
path via the material FK. Query = **exact** vector search filtered by material and owner. No ANN
index — at 20–50 chunks per document, exact search is correct and faster than maintaining HNSW,
and HNSW build memory is a real hazard at 512 MB. No hybrid, no reranking (§2/O4).

**Isolation enforced structurally.** A manager that always joins through `StudyMaterial` to the
owner, so a query that forgets the filter cannot compile rather than silently leaking.

**Flag lifecycle.** Every flag gets an entry with an expiry date. At expiry the flag is either
removed or its default is promoted. This also forces a decision on
`CURRICULUM_GATE_ENFORCE`, off "until after the interview" since July.

### Risks
| # | Risk | Sev | Mitigation |
|---|---|---|---|
| C-R1 | Embedding backfill exhausts quota | Medium | Resumable, batched, progress-logged; prioritise materials with recent RAG activity. |
| C-R2 | Chunk table grows unbounded | Medium | Cascade-delete on material delete; add a retention note; revisit if it exceeds a stated size. |
| C-R3 | Stale chunks after replacement | Medium | Delete-then-reinsert on material change; explicit test on the replace path. |
| C-R4 | **Cross-user chunk leakage** | **High** | Owner-scoped manager, not just a filter. Explicit isolation test. |
| C-R5 | Old RAG path becomes permanent | Low | Flag register entry with an expiry that forces removal. |

### Testing strategy
- Chunking deterministic; expected chunk count for known input; embedding dimension 512.
- Upload → chunks persisted. Replace → old chunks gone. Delete → cascade.
- **Isolation:** a query as user A never returns user B's chunks. Security-critical; explicit.
- Performance: `/api/ai/doubt/rag/` p50 before and after — the headline number.
- Backfill: resumable; interrupting and restarting does not duplicate chunks.
- Regression: full suite; latency gate.

### Rollback strategy
1. `RAG_USE_PERSISTED_INDEX=false` restores the re-chunk path, kept for **one release with a
   registered expiry**.
2. Chunk table is additive — leave it populated; re-embedding is quota-expensive.
3. Flag register is documentation; no runtime effect.

### Success criteria
| # | Criterion |
|---|---|
| C-S1 | 100% of materials indexed; no per-request extraction in the query path |
| C-S2 | `/api/ai/doubt/rag/` p50 reduced ≥ 40% vs baseline |
| C-S3 | Cross-user isolation test green |
| C-S4 | Flag register exists; every flag has an owner and expiry; `CURRICULUM_GATE_ENFORCE` resolved |
| C-S5 | 220+ tests green; **no p50 regression > 10%** |

---

## Part III — Deferred, With Triggers

Following architecture §15's convention: **triggers are metrics, not dates.**

| Item | Trigger to build |
|---|---|
| **Two-sided Elo rating updates** | Median attempts per servable question ≥ 10 |
| **Exploration (epsilon-greedy / Thompson)** | ≥ 200 monthly active users |
| **Propensity float** | Ships with exploration — meaningless before it |
| **Calibration study** | ≥ 500 labelled outcomes (currently 31) |
| **httpOnly refresh cookie** | Before any public launch, or first non-test user data of value |
| **Hybrid retrieval + reranking** | RAG scope extends beyond one selected document |
| **Reference-solution verification** | A reference-solution corpus exists to verify against |
| **Async grading (Celery)** | Paid instance, or > 2k submissions/day (currently ~40 total) |
| **Transformer knowledge tracing** | ≥ 50k submissions |
| **Read replica / sharding** | Database appears in a latency breakdown as the top stage |

### The uncomfortable observation

At 19 users and 40 all-time submissions, **the binding constraint on SparkLM is not engineering.**
Every item deferred above is deferred for the same reason: there is no usage to justify it. The
highest-value work available is not on this plan — it is content quality (2,926 questions, ~35%
multi-language coverage, a large placeholder backlog) and getting the product in front of people.

That is outside an engineering milestone's remit, and it should be said anyway, because a plan
that builds sophisticated adaptive infrastructure for 19 users is optimising the wrong system.
M4 as approved is deliberately small: close real risks, stop real waste, accumulate cheap
observations, and **stop building for a scale that does not exist.**

---

## Part IV — Execution Order and Gates

```
Phase A (days)      →  Phase B (1–2 wks)   →  Phase C (~1 wk)
Safety Closeout        Operational Truth      RAG Index & Flags
      │                       │                      │
      └── gate: no p50 regression > 10% at every phase boundary
```

**Standing constraints:**
- Production concurrency tests capped at **15**. The 40-way burst is not repeated.
- Every new setting gets a boot probe or a test — configuration that is never invoked looks
  identical to configuration that works.
- Every constraint-pinning test is mutation-verified.
- Every new flag enters the register with an expiry, in the same commit.
- No phase ships without the latency gate green.

---

*Approved. Implementation may begin at Phase A on your go-ahead.*
