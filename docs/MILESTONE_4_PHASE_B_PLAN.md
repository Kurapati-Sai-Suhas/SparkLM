# SparkLM — Milestone 4 Phase B Design Document

**Status:** Design only. No code.
**Baseline:** `9878457` — Phase A complete, deployed, approved.
**Theme:** *Operational Truth* — make what already exists actually run, and prove it.

---

## §0. Two findings from reviewing the repository

The approved M4 plan sketched Phase B before this review. Two of its assumptions did not survive
contact with the code.

### Finding 1 — `send_spaced_repetition` is a stub that would push fabricated notifications

The plan said "schedule the decay jobs." One of those two commands is not implemented:

```python
for user in users:
    # NOTE: Replace this mock data with your actual UserTopicMastery query
    # mastery_records = UserTopicMastery.objects.filter(user=user)
```

It iterates **every user** and pushes WebSocket notifications derived from **mock data**, with the
real mastery query commented out. Scheduling it as written would send every user fabricated
"time to review X" prompts on a timer.

Following the approved plan literally would have shipped that. It is now a Phase B work item in
its own right, and the schedule covers `calculate_decay` only until it is fixed.

`calculate_decay` by contrast is real and safe: it delegates to `EloEngine.apply_time_decay`,
charges only after 7+ days of inactivity, sizes the penalty linearly, and checkpoints via
`UserTopicMastery.last_decay_applied_at` so re-running never double-charges.

### Finding 2 — the planned item counters are derivable, so they are not "irreversible"

The plan justified adding `Question.attempt_count` / `correct_count` now on the grounds that
observations must start accumulating early. That reasoning is wrong. Both are pure aggregates over
data already persisted on every submission:

```sql
SELECT question_id, COUNT(*), COUNT(*) FILTER (WHERE status = 'accepted')
FROM groups_codesubmission GROUP BY question_id;
```

`CodeSubmission` already records user, question, status and timestamp for every attempt. The
counters can be backfilled exactly, at any future date. Nothing is lost by waiting — and adding
them now would add two more hand-maintained denormalised counters to a codebase whose debt
register already lists counter drift (D13) as an open defect.

**Deferred.** The genuinely unreconstructible item is `policy_version` (§2, B4), which depends on
code state at decision time and is stored nowhere.

### Measured state (production, this review)

| | |
|---|---|
| Users / submissions | 19 / 40 |
| Questions | 2,926 — **1,784 servable (61%)**, 1,141 placeholder |
| Topics with servable content | 20 of 22 |
| `UserTopicMastery` rows | 66 — **60 never decayed** |
| Scheduled workflows | `ci.yml`, `keepalive.yml` — **no maintenance schedule** |
| Frontend test runner | **none** |

Content is healthier than assumed: 61% servable across 20 of 22 topics is a workable pool for 19
users. **Content work is therefore not Phase B.** The decay figure confirms the opposite — the
retention model has effectively never run in production.

---

## §1. Phase B Objectives

| # | Objective |
|---|---|
| **O1** | Time-dependent models run on a schedule, and a job that stops is visible without anyone looking. |
| **O2** | No scheduled job pushes fabricated data to users. |
| **O3** | Frontend domain logic has automated tests and a CI gate. |
| **O4** | Routing decisions record which policy produced them. |
| **O5** | Operational state (admission shedding, maintenance freshness, cache health) is observable without a shell. |

**Hard gate, carried from Phase A:** no net latency regression. `/healthz`, `/api/token/` and
`/api/code/next/` p50 within 10% of baseline (391 / 551 / 1221 ms).

---

## §2. Feature List, with the five-question test

Each candidate answered against: *(1) needed today? (2) enough data? (3) improves UX?
(4) improves engineering quality? (5) measurable?*

| ID | Feature | 1 | 2 | 3 | 4 | 5 | Priority |
|---|---|---|---|---|---|---|---|
| **B1** | Consolidated `run_maintenance` command (decay) + heartbeat | ✅ | n/a | ✅ | ✅ | ✅ | **P0** |
| **B2** | Scheduled workflow invoking it | ✅ | n/a | ✅ | ✅ | ✅ | **P0** |
| **B3** | Fix `send_spaced_repetition` mock-data stub | ✅ | ✅ | ✅ | ✅ | ✅ | **P0** |
| **B4** | Frontend test runner + extract portal logic + tests | ✅ | n/a | ➖ | ✅ | ✅ | **P0** |
| **B5** | `RecommendationLog.policy_version` | ➖ | n/a | ❌ | ✅ | ✅ | **P1** |
| **B6** | Ops snapshot on `/healthz` (staff-gated detail) | ✅ | n/a | ❌ | ✅ | ✅ | **P1** |
| **B7** | Language registry (single source of truth) | ➖ | n/a | ➖ | ✅ | ✅ | **P1** |
| **B8** | `rebuild_counters` command for derived counters | ➖ | n/a | ❌ | ✅ | ✅ | **P2** |
| **B9** | Correct 500 → 409 for misconfigured question | ➖ | n/a | ➖ | ✅ | ✅ | **P2** |
| **B10** | Remove vestigial `UserTopicMastery.elo_rating` | ➖ | n/a | ❌ | ✅ | ➖ | **P2** |

---

## §3. Why each feature belongs in Phase B

**B1 / B2 — maintenance job and schedule (P0).**
60 of 66 mastery rows have never been decayed. `/api/review/queue/` reads from that model, so the
spaced-repetition feature currently surfaces state that has not been updated since the rows were
created. This is not a future improvement; it is a shipped feature that does not work. The
heartbeat matters as much as the job: an unscheduled job was invisible for months, and a *silently
stopped* job would be equally invisible without one.

**B3 — the mock-data stub (P0).**
Discovered in this review (§0). It is P0 not because the feature is urgent but because B2 makes it
dangerous: scheduling a job that fabricates user-facing notifications is worse than not scheduling
anything. Either it is implemented against `UserTopicMastery`, or it is excluded from the schedule
and marked unimplemented. Both are acceptable; silently scheduling it is not.

**B4 — frontend tests (P0).**
`AdaptiveCodingPortal.tsx` holds the logic that decides *what a student sees in the editor* —
`templateFor`, `availableLanguages`, `SELF_CONTAINED_LANGUAGES`, `EMPTY_STUB`. The empty-editor
defect that Milestone 2 fixed lived exactly here. It has zero automated coverage, and Phase A just
modified `main.tsx` with no test to catch a regression. The functions are pure and currently
module-private, so this is a small extraction plus a runner — the cheapest large risk reduction
available.

**B5 — `policy_version` (P1).**
The one piece of instrumentation that genuinely cannot be backfilled: it depends on which code was
running at decision time. Phase C and M5 both change routing; without it, the before/after boundary
in 177 (and growing) `RecommendationLog` rows is unrecoverable. One nullable column, one constant,
one write.

**B6 — ops snapshot (P1).**
Every milestone has been slowed by the same gap: no way to see runtime state without a shell.
Phase A added admission control that sheds requests — nobody can currently tell whether it has ever
fired. Scoped deliberately small: counters already in memory, exposed on the existing health
endpoint, no new dependency, no metrics stack.

**B7 — language registry (P1).**
Language identity is a bare string in four places (serializer validation, `LANGUAGE_IDS`,
`WRAPPER_LANGUAGE_ALIASES`, frontend `BOILERPLATE_KEYS`). That divergence has caused **three**
production bugs: `js`/`javascript` mapping to Judge0 ID 63 in one map but not the other, wrapper
alias mismatches, and boilerplate key mismatches. Three symptoms of one cause is a pattern, not bad
luck.

**B8 / B9 / B10 — P2 hygiene.**
Small, safe, each closing a named debt item. Ship if the phase has room; drop without renegotiation
if it does not.

---

## §4. Alternatives considered · §5. Why rejected

| Alternative | Rejected because |
|---|---|
| **Celery beat** for scheduling | Requires a worker tier and a second Redis; the architecture places both in Phase C+. GitHub Actions cron already runs the warm-keeper successfully. Reaching for infrastructure we do not have, to schedule one daily job, is the wrong trade. |
| **Django-cron / APScheduler in-process** | An in-process scheduler on a single instance that sleeps after 15 minutes idle will not fire reliably — the same reason the warm-keeper exists. It would also consume the one serialised worker. |
| **Two separate scheduled workflows** (decay, notifications) | Doubles the surface that can silently stop, and the M4 review already criticised adding a second unmonitored periodic job while listing unscheduled jobs as critical debt. One command, one schedule, one heartbeat. |
| **Add `attempt_count` / `correct_count` now** | Derivable from `CodeSubmission` (§0), so nothing is lost by waiting — and it would add two more hand-maintained counters to an existing counter-drift defect. |
| **Propensity float alongside `policy_version`** | Meaningless while the router is deterministic: it would log a constant 1.0. It ships with exploration, which needs a user population that does not exist. |
| **Prometheus / OpenTelemetry** for B6 | A metrics agent costs the memory it would measure on a 512 MB instance, and needs a collector to point at. Three integers on an existing endpoint answers today's questions. |
| **Jest** for B4 | The project is Vite-based; Vitest shares the config, transform pipeline and path aliases. Jest would need a parallel toolchain. |
| **Full component/E2E tests** (Playwright) for B4 | The valuable logic is pure functions. Component and browser tests cost far more to write and maintain, and would not have caught the empty-editor bug any earlier than a unit test on `templateFor`. |
| **Content backfill** of the 1,141 placeholders | 61% servable across 20 of 22 topics is sufficient for 19 users. Quota-bound LLM work with no user-facing urgency. |
| **Two-sided Elo / exploration / calibration** | No data. 40 submissions across 2,926 questions. Deferred with metric triggers (§14). |

---

## §6. Dependencies

- **B2 depends on B1** (the command must exist before it is scheduled) **and on B3** (do not
  schedule a job that fabricates data).
- **B4 is fully independent** — frontend only, no backend coupling. Can run in parallel or ship first.
- **B5, B7 independent.** B7 touches both tiers but changes no behaviour.
- **B6 depends on B1** only for the heartbeat field it reports.
- **No external dependencies.** No new services, no paid plan, no infrastructure.
- **New dev dependencies:** `vitest`, `@testing-library/react`, `jsdom` (B4). Dev-only — they do
  not enter the production bundle.

---

## §7. Files likely to change

| Area | Files |
|---|---|
| B1 | `common/management/commands/run_maintenance.py` (new); `common/models.py` (new — heartbeat row) or a cache-backed marker |
| B2 | `.github/workflows/maintenance.yml` (new) |
| B3 | `groups/management/commands/send_spaced_repetition.py` |
| B4 | `studysphere-ai-11/vitest.config.ts` (new); `src/lib/editorTemplates.ts` (new — extracted); `src/pages/AdaptiveCodingPortal.tsx` (imports only); `src/lib/__tests__/editorTemplates.test.ts` (new); `package.json`; `.github/workflows/ci.yml` |
| B5 | `groups/models.py` + migration; `groups/hybrid_router.py`; `groups/coding_views.py` |
| B6 | `common/health.py`; `common/middleware.py` (counter only); `common/apps.py` |
| B7 | `common/languages.py` (new); `groups/serializers.py`; `groups/coding_views.py`; `groups/services.py`; `src/lib/editorTemplates.ts` |
| B8 | `groups/management/commands/rebuild_counters.py` (new) |
| B9 | `groups/coding_views.py` |
| B10 | `groups/models.py` + migration |

**Explicitly not touched:** authentication, JWT, revocation, admission-control logic, throttling,
hashers, CSP, connection pooling, Judge0 execution, the wrapper system, RAG, LLM services.

---

## §8. Database changes

Additive only. No backfill required, no data migration, no destructive operation.

| Change | Type | Notes |
|---|---|---|
| `RecommendationLog.policy_version` | `CharField(max_length=32, null=True)` | Nullable so existing 177 rows remain valid; a null means "pre-B5". |
| Maintenance heartbeat | new single-row model **or** cache key | Prefer a durable row: a cache-backed heartbeat vanishes on a Redis flush and would read as "job stopped". |
| `UserTopicMastery.elo_rating` removal (B10, P2) | `RemoveField` | Only if P2 is reached. **Destructive** — the one change in Phase B that cannot be rolled back by config, so it ships last or not at all. |

Migrations run in the Render start chain, which is safe at one instance. All Phase B migrations are
additive and tolerated by older code, satisfying the expand-migrate-contract rule.

---

## §9. API changes

| Endpoint | Change | Compatibility |
|---|---|---|
| `GET /healthz` | Adds fields to the JSON body (heartbeat age, admission counters). Status semantics **unchanged** — still 200/503 on database reachability alone. | Additive. Render's health check reads status, not body. |
| `GET /api/code/next/` | No contract change. `policy_version` is recorded server-side, not returned. | None. |
| `POST /api/code/submit/` | `500` → `409` for a question with no test cases (B9, P2). | Behavioural. A data-integrity condition stops being reported as a server fault. |

**No new endpoints. No breaking changes.**

⚠ The health payload must **not** become a staff-only endpoint or gain authentication — Render polls
it unauthenticated. Detailed operational fields beyond the counters belong behind staff auth if they
ever grow.

---

## §10. Testing strategy

**B1 / B2 — maintenance**
- Idempotence: running twice applies decay once (the `last_decay_applied_at` checkpoint).
- Partial failure: one sub-task raising does not prevent the others, and the heartbeat still records
  the attempt with its outcome.
- Heartbeat freshness: a stale heartbeat is reported and does **not** make `/healthz` return 503
  — advisory, matching the boot-probe precedent.
- Mutation: break the checkpoint and assert the double-charge test fails.

**B3 — spaced repetition**
- Notifications are derived from real `UserTopicMastery` rows, not constants.
- A user with no decayed topics receives **nothing** (the current stub's worst behaviour).
- Regression test asserting the mock-data path is gone.

**B4 — frontend**
- `templateFor` returns the stored template, falls back to `EMPTY_STUB`, and returns null when
  neither exists.
- `availableLanguages` excludes templateless languages.
- C/C++ receive self-contained skeletons with `main()`; Python/Java/JS receive method stubs.
- Alias resolution: `js` and `javascript` resolve identically.
- CI job runs on every push, parallel to the Python job.

**B5 — policy version**
- 100% of new rows carry it; changing the constant affects new rows only.

**B6 — ops snapshot**
- Counters increment on shed; the payload is well-formed when admission control is disabled.
- `/healthz` status code semantics unchanged (guard against scope creep into a dependency check).

**B7 — language registry**
- Every language the serializer accepts has a Judge0 ID, a wrapper strategy and a frontend key —
  the test that would have caught all three historical bugs.

**Standing:** every constraint-pinning test mutation-verified; production concurrency checks capped
at **15**; full suite green (245 baseline) before each phase item merges.

---

## §11. Rollback strategy

Ordered by cost, cheapest first. Every P0 item is reversible without a deploy.

| Item | Rollback |
|---|---|
| B2 | Disable the workflow in GitHub. Instant, no deploy. |
| B1 | Command stops being invoked; it has no request-path involvement. |
| B3 | Revert to not scheduling it — the command is only reachable from the scheduler. |
| B4 | Remove the CI job. The extraction is a pure move; behaviour is unchanged either way. |
| B5 | Additive nullable column; stop writing it. **Do not reverse the migration** — dropping it discards recorded attribution. |
| B6 | Feature-flag the extra payload fields off; the endpoint reverts to `{"status": "ok"}`. |
| B7 | Registry is a pure refactor with no behaviour change; revert the commit. |
| B10 | ⚠ **Not reversible by config.** Field removal loses the column. Ship last, or defer. |

**Flag discipline (carried from the M4 plan):** any flag added in Phase B enters
`docs/FEATURE_FLAGS.md` with an owner and expiry **in the same commit**.

---

## §12. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | **B3 ships still fabricating data** | **High** | B2 must not schedule it until B3 lands. Explicit dependency; the schedule covers `calculate_decay` only in the interim. |
| R2 | Decay runs on production for the first time and moves 60 mastery rows at once | **Medium** | It is checkpointed and capped at 2 Elo/day after a 7-day grace. Dry-run mode first; report the intended delta before applying. |
| R3 | GitHub Actions cron throttling delays the job | Low | Measured 54–213 min gaps against a 5-min schedule. Decay is daily-granularity, so lateness is tolerable — and the heartbeat makes it visible, unlike the warm-keeper case which needed an in-run loop. |
| R4 | The B4 extraction changes behaviour | Medium | Pure move; the suite must pass before and after with no test edits — the same bar Milestone 3's service extraction met. |
| R5 | Ops snapshot leaks information to unauthenticated callers | Medium | Counters only (integers). No usernames, no config, no versions. `/healthz` is public by design and must stay minimal. |
| R6 | B7 misses a call site and a language silently stops working | Medium | The registry test enumerates every accepted language across all four maps; that is the acceptance criterion, not the refactor itself. |
| R7 | Scope creeps toward adaptive-learning work | Medium | §14 lists deferrals with metric triggers. Anything requiring behavioural data is out by definition. |

---

## §13. Success criteria

| # | Criterion | Measurement |
|---|---|---|
| S1 | Maintenance runs daily | Heartbeat age < 36 h on `/healthz` for 7 consecutive days |
| S2 | A stopped job is visible | Deliberately disabled run detected via heartbeat age, without inspecting logs |
| S3 | Decay is applied | `UserTopicMastery.last_decay_applied_at` non-null for eligible rows; "never decayed" falls from 60 |
| S4 | No fabricated notifications | Every notification traceable to a real mastery row; zero sent to users with no decayed topics |
| S5 | Frontend tested | Vitest job in CI; the four template/language functions covered; job fails on a deliberately broken `templateFor` |
| S6 | Attribution recorded | 100% of new `RecommendationLog` rows carry `policy_version` |
| S7 | Operations observable | Admission shed count and heartbeat age readable without a shell |
| S8 | No regression | 245+ backend tests green; production battery 13/13; **no p50 regression > 10%** |

---

## §14. Explicitly deferred, with triggers

| Item | Trigger |
|---|---|
| `Question.attempt_count` / `correct_count` | Ships **with** two-sided Elo — derivable, so no reason to precede it |
| Two-sided Elo rating updates | Median attempts per servable question ≥ 10 (currently ~0.02) |
| Exploration + propensity float | ≥ 200 monthly active users |
| Calibration study | ≥ 500 labelled outcomes (currently 31) |
| httpOnly refresh cookie | Before public launch, or first user data of real value |
| Persisted RAG index, hybrid retrieval, reranking | Phase C |
| Reference-solution test-case verification | A reference-solution corpus exists |
| Async grading (Celery) | Paid instance, or > 2k submissions/day (currently ~40 total) |
| Content backfill of 1,141 placeholders | Servable pool drops below ~50%, or a topic runs dry |
| Judge0 circuit breaker | Admission control now bounds the blast radius; revisit if a Judge0 outage causes a user-visible incident |
| Staging environment | Before the first change that cannot be verified in production safely |

---

## §15. Assessment

**Estimated duration:** **6–9 working days.**
P0 (B1–B4) is 4–6 days, of which B4 is roughly half — the extraction plus first-ever frontend test
setup carries the most unknowns. P1 (B5–B7) is 2–3 days; B7 is the largest because it spans both
tiers. P2 is opportunistic.

**Estimated complexity:** **Low–Medium.** No new architecture, no new services, no algorithms.
The only cross-tier item is B7, and it is a pure refactor. Complexity is concentrated in B3
(reimplementing a stub correctly) and B4 (new tooling in a codebase that has never had it).

**Expected impact:**

| Dimension | Impact |
|---|---|
| User-facing | **Moderate** — the review queue starts reflecting reality; no fabricated notifications |
| Engineering quality | **High** — first frontend tests; a whole class of unscheduled-job debt closed |
| Operational | **High** — silent job failure becomes visible; admission control becomes observable |
| Performance | **Neutral by design** — the latency gate forbids regression, and nothing is added to the request path except two integer increments |
| Security | **Neutral** — Phase B touches no security surface |

**Principal risks:** R1 (scheduling the stub) is the one that could actively harm users, and it is
fully mitigated by an ordering constraint. R2 (first production decay) is the one that touches real
learner state and is mitigated by a dry run.

---

## Is Phase B appropriately scoped?

**Yes — after two reductions made during this review.**

It was not, as drafted in the M4 plan. Two problems:

1. It would have **scheduled a mock-data stub**, pushing fabricated notifications to every user.
   Now a P0 work item with an explicit ordering dependency.
2. It included **item counters justified by reasoning that does not hold** — they are derivable
   from `CodeSubmission` and therefore lose nothing by waiting, while adding to an existing
   counter-drift defect. Deferred to ship alongside two-sided Elo.

What remains passes the five-question test honestly:

- **Nothing here needs behavioural data.** Scheduling, tests, a version string, and three integers.
- **Everything is measurable** — S1–S8 are all observable without new infrastructure.
- **Every P0 is reversible without a deploy**, and the single irreversible item (B10) is P2 and
  sequenced last.
- **The largest item (B4) has the largest risk reduction**, and the smallest items are hygiene that
  can be dropped without renegotiating the phase.

Four P0 items, three P1, three droppable P2, in 6–9 days, with no new services. That is a phase
that can actually be finished — which is the property the M4 review found the draft lacking.

---

*Design complete. No code written. Awaiting approval to implement Phase B.*
