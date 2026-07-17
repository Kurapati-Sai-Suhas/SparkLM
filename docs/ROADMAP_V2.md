# SparkLM v2 — Implementation Roadmap (order of record)

Companion to the frozen [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md). The spec defines *what*;
this document is the authoritative *order*. Reordered 2026-07-17: the July 23 interview
requires a live product by July 20, so Deploy stays first and the most demoable learner
feature (Effective mastery + Review Queue, formerly M11) is pulled forward to M7. The
remaining milestones slide down with their internal order preserved.

Each milestone follows the same protocol: plan → approval → implementation →
principal-engineer review with required fixes → observed-green CI before closure.

## Done (Phase A — all §2.4 engineering mandates complete)

| # | Milestone | Closed | Key commits |
|---|-----------|--------|-------------|
| M1 | Security & privacy trio (hidden test cases → `sample_case`; staff-only telemetry; auth throttles + XFF anti-spoofing) | 2026-07-16 | `0165af5` … `0c8b643` |
| M2 | Index catalog (§4.4) + monthly partitioning of `CodeSubmission` (§4.3) with self-healing maintenance | 2026-07-16 | `350efd6`, `19dfcc9` |
| M3 | Service-layer extraction: `GradingService` + `ProgressionService` (§2.4.6, pure move, zero test edits) | 2026-07-16 | `6ec7359` |
| M4 | Row-locked learner-state transactions + threaded race tests (§2.2); decay sweep locked in review | 2026-07-16 | `052dac7`, `5814c0e` |
| M5 | Traffic-Cop statistic v2: Wald–Wolfowitz runs test (§6.2), versioned artifact registry + eval gate (§5), `learning/` package begins (§9) | 2026-07-17 | `e7caf1e` |

## Upcoming (reordered 2026-07-17)

| # | Milestone | Was | Scope anchor |
|---|-----------|-----|--------------|
| **M6** | **Deploy to production** — LIVE at sparklm-api.onrender.com + spark-lm-3y3e.vercel.app (2026-07-17; smoke fixes: boot-time URLconf warmup, caseless-question quarantine, DAG UI repairs, keep-alive pinger). Remaining user actions: ALLOWED_HOSTS for WebSockets, optional region move | M6 | §15 Phase B topology |
| **M7** | **Effective mastery + Review Queue** — SHIPPED 2026-07-17: learning/memory.py retention math, /api/review/queue/, Due-for-Review card, real router-panel telemetry, registration email validation. Curriculum re-locking staged behind CURRICULUM_GATE_ENFORCE (off until after the interview) | M11 | §6.2 |
| M8 | Observability + backups — SHIPPED 2026-07-17 with M7: Sentry (DSN-gated), per-request access log, backup runbook + Neon PITR docs | M7 | §12 |
| M9 | Auth v2 (httpOnly refresh) | M8 | §13.2 |
| M10 | Two-sided Elo + item calibration | M9 | §6.2 |
| M11 | Belief layer (GDCP retirement; observed accuracy becomes immutable) | M10 | §4.2, §6.2 |
| M12 | Propensity logging + calibration study | M12 | §4.2, §6.4 |
| M13 | Thompson router + OPE harness | M13 | §5 |
| M14 | Misconception fingerprints | M14 | §6.4 |
| M15 | Async grading (Celery) | M15 | §2.3 |
| M16 | Content queue + isomorph pipeline | M16 | §8 |

Dependency note honored by the reorder: the pulled-forward M7 initializes skill from the
frozen mastery rule (§6.2 explicitly defines this initialization), so it does not depend
on the belief layer (now M11); when M11 lands, effective mastery upgrades in place.
