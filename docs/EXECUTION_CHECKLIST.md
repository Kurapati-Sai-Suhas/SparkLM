# SparkLM — Execution Checklist

**The project's execution tracker.** Tick tasks as they merge, never before.
Detail for every phase lives in `EXECUTION_ROADMAP.md`.

**Rule:** one phase = one branch = one PR = one revert. Never combine phases.

Legend — ⬜ Not Started · 🔄 In Progress · ✅ Completed · ⏸ Deferred · ❌ Removed

---

## M0 — Baseline Reconciliation ✅ *(0.5 ed)* — merged `5a57565`, 2026-08-09

- [x] **P0.1 Merge N3 Phase 1a** — `m6-phase1a-fix-broken-pages` → `main`
  - [x] T0.1.1 Confirm Render `autoDeploy` — not set in `render.yaml`; Render default (`true`) applies
  - [x] T0.1.2 Merge with a merge commit — `5a57565`, parents `04aab1e` + `3d88a8d`, tree `ddbef0e`
  - [x] T0.1.3 Verify the three pages — **deployed-artifact + runtime verification done**; see caveat
  - [x] T0.1.4 CI green on `main` — all 3 jobs pass (run `31300049244`)

**P0.1 evidence.** Pre-merge: 104 tests, typecheck clean, production build green,
merge tree byte-identical to the reviewed tree, 3/3 guard mutants killed.
CI: Frontend Vite Build ✓ · Backend Tests (pytest + Postgres) ✓ · pip-audit ✓.
Deployed verification: all three page chunks contain **zero** relative `/api`
references and **zero** `Bearer`/`localStorage` references; the deployed client
base is `https://sparklm-api.onrender.com/api`; runtime shows **zero**
Vercel-origin `/api` requests; `/settings` correctly redirects when signed out.
The trap remains provably live — Vercel `/api/notifications/` returns
**HTTP 200 `text/html`** while Render returns **401 `application/json`**.

> **T0.1.3 caveat.** Signed-out verification is complete. Verifying the three
> pages *rendering real data while signed in* requires a login and was not
> performed. Owner: repository owner.

**Console errors seen on the deployed app are pre-existing and unrelated to
P0.1**: a CSP block on `accounts.google.com/gsi/style` (from `style-src` in
`vercel.json`, unchanged by this merge) and a Google Sign-In 400. Logged as
debt, not a P0.1 regression.

## M1 — Surface Reduction 🔄 *(5 ed)* — P1.1 done, P1.2 open

- [x] **P1.1 Retire the un-runnable ML stack** — merged `40b4ba5` (PR #7), 2026-08-09
  - [x] T1.1.1 Deleted `gnn_engine`, `shap_explainer`, `export_onnx`, `mirt_engine`
  - [x] T1.1.2 **CORRECTED** — deleted `synthetic_data_generator` + 9 GCN artifacts +
        an orphaned `prerequisite_model.pth`. **`retrain_ai.py` KEPT**: preflight proved
        it trains the *production* routing classifier (`joblib.dump` →
        `routing_classifier_v2.pkl` → `joblib.load` in `hybrid_router`) and that six
        tests exercise it. Its dead GCN + LSTM sections were excised instead.
  - [x] T1.1.3 Collapsed `_compute_xai` to the heuristic branch; schema pinned
  - [x] T1.1.4 **CORRECTED** — `requirements-ml.txt` reduced 5 → 2 packages, not deleted.
        `torch`/`transformers` still serve Visual Search (P9.3). Measured 53 MB off CI.
  - [x] T1.1.5 **CORRECTED** — `ENABLE_SHAP_XAI` was never in `settings.py`; removed from
        `render.yaml`, `.env.example`, `README.md`, `FEATURE_FLAGS.md`, `DEPLOYMENT.md`
        and the flag tests
  - [x] T1.1.6 Measured: onnxruntime 38 MB + torch-geometric 11 MB + shap 2 MB

**P1.1 evidence.** 679 backend tests (was 675) + 104 frontend; typecheck, build and
Django check clean; CI green on all 3 jobs on `main`. Production `/healthz` → 200 after
deploy, proving the backend boots without the deleted modules. Mutants killed: classifier
training removed · artifact write removed · contract body dropped · evaluation dropped ·
gate bypassed with fabricated metrics · torch reintroduced · XAI schema key renamed · flag
restored to the register. Two tests were added *because* mutation testing exposed that
deleting `joblib.dump` left all 675 tests green.
- [ ] **P1.2 Collapse coding surfaces; remove Flashcards** — `m1-p2-collapse-surfaces`
  - [ ] T1.2.1 Redirect `/code` → `/coding-portal`; delete `CodingPortal.tsx`
  - [ ] T1.2.2 Remove Flashcards page, `AIFlashcardView`, URL, nav entry
  - [ ] T1.2.3 Shrink Phase 1b allowlist 6 → 5

## M2 — Product Truth ⬜ *(12 ed)*

- [ ] **P2.1 Pagination correctness** — `m2-p1-pagination`
  - [ ] T2.1.1 Raise `PAGE_SIZE`; explicit per-view sizes
  - [ ] T2.1.2 Guard test (mutation: restore `PAGE_SIZE: 3` → must fail)
  - [ ] T2.1.3 Frontend paging where lists exceed one page
- [ ] **P2.2 Streaks and badges in the transaction** — `m2-p2-gamification`
  - [ ] T2.2.1 Streak update under the existing profile lock
  - [ ] T2.2.2 `seed_badges` command (idempotent)
  - [ ] T2.2.3 Award rules in `apply_submission`
  - [ ] T2.2.4 Threaded race test — no double award
- [ ] **P2.3 Notifications producer** — `m2-p3-notifications`
  - [ ] T2.3.1 `notify()` service — persist then push
  - [ ] T2.3.2 Producers: badge earned, review due, streak at risk
  - [ ] T2.3.3 Paginate `NotificationView`
  - [ ] T2.3.4 Reconcile the spaced-repetition WebSocket push
- [ ] **P2.4 Dashboard aggregates + ErrorBoundary** — `m2-p4-dashboard`
  - [ ] T2.4.1 Real `quizzes_taken`, achievement points
  - [ ] T2.4.2 Delete the Study Hours tile and the false comment at `Dashboard.tsx:28`
  - [ ] T2.4.3 Surface weak topics from `_build_recommendation`
  - [ ] T2.4.4 Route-level `ErrorBoundary`

## M3 — Coding Platform Integrity ⬜ *(11 ed)*

- [ ] **P3.1 Judge0 batch grading** — `m3-p1-judge0-batch`
  - [ ] T3.1.1 Batch submission API behind a flag
  - [ ] T3.1.2 Partial-failure tolerance
  - [ ] T3.1.3 Preserve the injected-runner test seam
  - [ ] T3.1.4 Verdict parity across all five statuses
  - [ ] T3.1.5 Record measured latency change
- [ ] **P3.2 Harness entry-point correctness** — `m3-p2-harness-entrypoint`
  - [ ] T3.2.1 Add `Question.entry_point` (nullable) + migration
  - [ ] T3.2.2 Use it in Python, Java and JS harnesses
  - [ ] T3.2.3 Backfill command; spot-check 20 questions
  - [ ] T3.2.4 Test proving the alphabetical `dir()` bug is fixed
- [ ] **P3.3 Submit/next path hygiene** — `m3-p3-submit-hygiene`
  - [ ] T3.3.1 Idempotency key on submit
  - [ ] T3.3.2 `EXISTS` anti-join in `NextProblemView`
  - [ ] T3.3.3 Remove in-request `generate_test_cases`
  - [ ] T3.3.4 Replace the `Topic.objects.first()` fallback with a 404

## M4 — Evaluation Foundation ⬜ *(4 ed)*

- [ ] **P4.1 Offline evaluation harness** — `m4-p1-eval-harness`
  - [ ] T4.1.1 Metric definitions, reviewed before use
  - [ ] T4.1.2 Replay over `RecommendationLog` with confidence intervals
  - [ ] T4.1.3 LLM output evals usable as an M8 gate
  - [ ] T4.1.4 Commit a baseline report to `docs/evals/`

## M5 — Question Bank v2-lite ⬜ *(10 ed)*

- [ ] **P5.1 Test cases and examples as rows** — `m5-p1-testcase-rows`
  - [ ] T5.1.1 `TestCase` model with `is_public`; `QuestionExample`
  - [ ] T5.1.2 Lossless backfill from `hidden_test_cases`
  - [ ] T5.1.3 Repoint grading and `_sample_case`
  - [ ] T5.1.4 Verdict parity on 50 questions
- [ ] **P5.2 Hints, editorials, reference solutions** — `m5-p2-teaching-content`
  - [ ] T5.2.1 `Hint` (ordered, progressive)
  - [ ] T5.2.2 `Editorial` with complexity fields
  - [ ] T5.2.3 `ReferenceSolution` per language — the validation oracle
- [ ] **P5.3 Tags, difficulty, validation gate** — `m5-p3-taxonomy-gate`
  - [ ] T5.3.1 `QuestionTag` M2M + difficulty enum
  - [ ] T5.3.2 Automated validation gate (bad question must be rejected)
  - [ ] T5.3.3 `status` field replacing the `PLACEHOLDER_MARKER` heuristic

## M6 — Adaptive Learning: Measure & Operationalize ⬜ *(8 ed)*

- [ ] **P6.1 Measure the routing policy** — `m6-p1-routing-eval`
  - [ ] T6.1.1 Hierarchical vs flat on historical data
  - [ ] T6.1.2 Committed report; decision recorded (negative result is valid)
- [ ] **P6.2 MLOps for the shipped classifier** — `m6-p2-mlops`
  - [ ] T6.2.1 Artifact versioning for `RoutingClassifier`
  - [ ] T6.2.2 Input-distribution / drift monitoring
  - [ ] T6.2.3 Policy A/B switch
- [ ] **P6.3 Topic uniqueness + calibration groundwork** — `m6-p3-topic-calibration`
  - [ ] T6.3.1 `unique_together = ('portal','name')` + migration
  - [ ] T6.3.2 Decide and enforce `Topic.portal` nullability
  - [ ] T6.3.3 Item-calibration table

## M7 — AI Quality: Code Tutoring ⬜ *(8 ed)*

- [ ] **P7.1 Prompt versioning + regression tests** — `m7-p1-prompt-versioning`
- [ ] **P7.2 In-process hint ladder (retire the n8n dependency)** — `m7-p2-hint-ladder`
- [ ] **P7.3 Failure explanation from test output** — `m7-p3-failure-explain`

## M8 — Content Seeding to 500 ⬜ *(15 ed)*

- [ ] **P8.1 Seeder pipeline on the v2-lite schema** — `m8-p1-seeder`
- [ ] **P8.2 100 curated, 100% reference-verified** — `m8-p2-tier-100`
- [ ] **P8.3 250 with hints and editorials** — `m8-p3-tier-250`
- [ ] **P8.4 500 with calibrated difficulty** — `m8-p4-tier-500`

## M9 — Frontend Consolidation & Residual Removals ⬜ *(8 ed)*

- [ ] **P9.1 Phase 1b migration — allowlist reaches zero** — `m9-p1-phase1b`
- [ ] **P9.2 React Query: adopt or remove** — `m9-p2-data-layer`
- [ ] **P9.3 Remove Visual Search, Collab Workspace, Direct Chat** — `m9-p3-residual-removals`

## M10 — Launch Readiness ⬜ *(2 ed · GATE)*

- [ ] **P10.1 Infrastructure activation** — `m10-p1-activation`
  - [ ] T10.1.1 Schedule `flushexpiredtokens` **before** enabling rotation
  - [ ] T10.1.2 Provision bucket; set 5 `AWS_*` vars *(operator)*
  - [ ] T10.1.3 `migrate_media_to_object_storage --dry-run`, then run
  - [ ] T10.1.4 Verify an upload survives a redeploy
  - [ ] T10.1.5 Set `AUTH_V2_COOKIES=true`; verify cookie attributes and rotation
  - [ ] T10.1.6 Set `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` *(operator)*

### Track B — ⏸ triggered by >20 real users or any launch announcement *(~52 ed)*
- [ ] Capacity: paid tier, multi-worker, Redis in CI, load harness
- [ ] Async grading
- [ ] **Compliance: account deletion, data export, retention, consent (FERPA/GDPR)**
- [ ] Accessibility baseline (only 6 of 22 pages carry any a11y attribute)
- [ ] WebSocket rate limiting, content moderation, cost instrumentation
- [ ] API versioning + OpenAPI

## M11 — Performance, Observability & CI ⬜ *(10 ed)*

- [ ] **P11.1 CI hardening** — `m11-p1-ci`
  - [ ] Coverage gate · frontend lint · **Redis service** · mutation on changed files
- [ ] **P11.2 Retention and limits** — `m11-p2-retention`
  - [ ] `RecommendationLog` retention · WS caps · loud Redis failure
- [ ] **P11.3 API versioning + OpenAPI** — `m11-p3-api-version`

---

## Parked work (not on any phase branch)

- [ ] **`preserve/settings-email-2xx-test`** (`d7b0e20`) — a Settings email-alerts
  branch test written during N3 Phase 1a Task 4 mutation work and never
  committed. Found uncommitted in the working tree during the P0.1 adversarial
  review, where it silently changed the suite count 104 → 105. Deliberately
  **excluded** from the P0.1 merge so the reviewed tree stayed byte-identical.
  Decide: land it on top of Phase 1a, or drop the unreachable `else` branch
  from `Settings.tsx` entirely. Not P1.1 scope.

## Continuous (every session)

- [ ] Update `DAILY_PROGRESS.md` before the session ends
- [ ] Regenerate `NEXT_TASK.md`
- [ ] Archive `10_INTERVIEW_HANDBOOK_PART_*` to `docs/archive/`
- [ ] Mark `ROADMAP_V2.md` superseded
- [ ] Correct the `RAGService` docstring claiming retrieval it does not perform
- [ ] Replace `google-generativeai` (EOL) and `PyPDF2` (deprecated)
