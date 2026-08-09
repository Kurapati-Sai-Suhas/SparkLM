# SparkLM — Daily Progress Log

Newest first. Append one entry per working session, **before the session ends**.

**Entry template**
```
## YYYY-MM-DD — <title>
**Branch:** · **Phase:** · **Status:** shipped / in progress / blocked
**Done:**
**Evidence:** (test counts, measurements, commit SHAs)
**Learned:**
**Next:**
```

---

## 2026-08-09 — P1.1: un-runnable ML stack retired (corrected scope)
**Branch:** `m1-p1-remove-dead-ml-stack` · **Phase:** M1/P1.1 · **Status:** shipped (`40b4ba5`, PR #7)

**Done:** Removed the deep-learning/XAI stack that could never execute in
production, while preserving the sklearn routing classifier that does.

**The correction.** The roadmap said delete `retrain_ai.py` because it "exists
only to produce artifacts nothing can load." Preflight disproved it: the file
writes `routing_classifier_v2.pkl`, which `hybrid_router` loads in production,
and six tests exercise its sklearn half. It was split, not deleted — the dead
GCN loop (body only printed) and DKT/LSTM block (built a loss object and threw
it away) were excised. They were the sole reason the module imported torch.

**Evidence:** 679 backend tests (was 675), 104 frontend, typecheck/build/Django
check clean, CI green on all 3 jobs, `/healthz` 200 in production after deploy.
Measured 53 MB off the CI install (onnxruntime 38, torch-geometric 11, shap 2);
torch (503 MB) and transformers stay for Visual Search until P9.3.

**Learned:** Two things worth carrying forward. First, a filename does not tell
you what a module does — `retrain_ai` read as "the AI training thing" and was
half production infrastructure. Second, mutation testing found that deleting
`joblib.dump` — the single line that ships the production model — left all 675
tests green; the ingredients were tested and the result was not. The test I
then wrote passed for the wrong reason too, because `open(path,"w")` creates
the file before anything is written to it.

**Next:** P1.2 — collapse coding surfaces, remove Flashcards.

---

## 2026-08-09 — P0.1 executed: N3 Phase 1a merged and deployed
**Branch:** `main` · **Phase:** M0/P0.1 · **Status:** shipped (`5a57565`)

**Done:** Merged N3 Phase 1a into `main` with a merge commit, pushed, CI green
on all three jobs, deployed and verified. Parked one uncommitted test on its
own branch rather than folding it into the merge. Updated the tracker.

**Evidence:**
- Merge `5a57565`, parents `04aab1e` + `3d88a8d`, tree `ddbef0e` — **identical
  to the tree approved in the adversarial review**, proving no unrelated change
  entered the merge.
- Pre-merge: 104 tests, typecheck clean, build green, 3/3 guard mutants killed.
- CI run `31300049244`: Frontend Vite Build ✓ · Backend Tests (pytest +
  Postgres) ✓ · pip-audit ✓. The backend job closed the one gap I could not
  verify locally with Docker down.
- Deployed chunks `Schedule-DLFwwj87`, `Notifications-DPktr52f`,
  `Settings-BWCHQP-J`: **0** relative-`/api` hits, **0** `Bearer`/`localStorage`
  hits. Deployed client base is `https://sparklm-api.onrender.com/api`.
- Runtime: **0** Vercel-origin `/api` requests; `/settings` redirects correctly
  when signed out.
- Trap still provably live: Vercel `/api/notifications/` → 200 `text/html`;
  Render → 401 `application/json`.

**Learned:** An uncommitted test had been sitting in the working tree since an
earlier session and silently changed the suite count from 104 to 105,
contaminating a verification run. It was caught only because the count changed
without explanation. A test count that moves for no stated reason is a signal,
not noise — and on a OneDrive-synced tree, `git status` at the start of a
session is not a durable guarantee.

**Next:** P1.1 — retire the un-runnable ML stack.

---

## 2026-08-06 — CTO-level repository audit; roadmap replanned
**Branch:** `m6-phase1a-fix-broken-pages` · **Phase:** planning · **Status:** shipped (docs only)

**Done:** Full repository audit across backend, frontend, data model, auth,
security, WebSockets, Judge0, AI/RAG, adaptive learning, deployment, CI and
docs. Produced `MASTER_ENGINEERING_ROADMAP.md`, `PROJECT_STATUS.md`,
`TECHNICAL_DEBT.md` (34 entries), `NEXT_TASK.md`, `FUTURE_SCOPE.md` and this
log. No code modified.

**Evidence:**
- Backend 98 files / 12,854 lines, 498 test functions, 0.79 test:source ratio.
  Frontend 45 files / 10,782 lines, 93 `it()` blocks, 0.15 ratio.
- `PAGE_SIZE: 3` (`settings.py:412`) applies to 4 list views; the SPA unwraps
  `.results` but never follows `next`.
- `Notification.objects.create`, `UserBadge.objects.create` and any write to
  `current_streak`: **zero occurrences** repository-wide.
- `update_user_activity` (`views.py:1044`) is a stub; the SPA never calls it.
- Dashboard `study_hours`/`quizzes_taken`/`achievement_points` are literals
  (`dashboard_views.py:87-89`).
- Env-var diff: `AWS_*` (5), `AUTH_V2_COOKIES`, `EMAIL_HOST_*` are read in code
  and absent from `render.yaml` — two merged milestones are inert in production.
- `requirements-ml.txt` (torch, onnxruntime, shap, transformers) is not
  installed on the web tier; `ENABLE_SHAP_XAI=false`; `docs/evals/` is empty.
- `GradingService.grade` issues one Judge0 call per test case, `wait=true`,
  `timeout=15`, on a single thread-sensitive ASGI worker.
- CI has no Redis service; `CHANNEL_LAYERS` falls back to in-memory silently.

**Learned:** Engineering quality and product reality have diverged. The
codebase is disciplined; the visible product is substantially hollow. The
previous roadmap's ordering was driven by a job interview dated 2026-07-23 and
its rationale has expired, while two milestone numbering schemes had drifted
apart. Deprecated M11–M14 to `FUTURE_SCOPE.md`.

**Next:** Merge Phase 1a, then `PAGE_SIZE`. See `NEXT_TASK.md`.

---

## 2026-08-06 — N3 Phase 1a follow-up: axios guard + docs correction
**Branch:** `m6-phase1a-fix-broken-pages` · **Phase:** N3 1a · **Status:** shipped (`3d88a8d`)

**Done:** Extended the network guard from `fetch`-only to all direct
transports (axios imports in every form, `axios.create`). Added
`CodingPortal.tsx` to the Phase 1b allowlist (now 6 files). Corrected
`FRONTEND_NETWORK_ACCESS.md`, which had claimed all API access went through the
shared client — untrue and unenforced.

**Evidence:** guard 10 → 26 tests; suite 88 → 104; typecheck clean; build
green. Mutation testing showed three pre-existing assertions could not fail
for their stated reason (disabling each detector left the suite green);
17 inline fixtures now kill those mutants. Removed a whitespace-flattening
helper whose removal changed no result.

**Learned:** `expect(offenders).toEqual([])` is unfalsifiable on its own — a
working detector and a broken one both return empty. Guard detectors need
fixtures. Also: file-injection testing is unreliable in this working tree
because it sits on a OneDrive-synced path; it produced two phantom failures.

**Next:** merge to `main`.
