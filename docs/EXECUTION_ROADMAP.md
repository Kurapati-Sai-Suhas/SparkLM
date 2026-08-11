# SparkLM — Final Execution Roadmap

**Status:** LOCKED 2026-08-06. Do not redesign unless implementation proves an
item impossible or technically incorrect.
**Supersedes:** the ordering sections of `MASTER_ENGINEERING_ROADMAP.md`.
**Basis:** Product Architecture Decision (E1) + Design Review Board amendments.

**Premise:** Track A (portfolio / pre-launch). Repository evidence: 177 total
`RecommendationLog` rows; three core pages broken for months unreported.
**Track B trigger:** the first cohort of >20 real users, or any external launch
announcement. Track B is pre-planned in §M10 and must be executed *before*
users arrive, not after.

**Totals:** 31 phases · 12 milestones · **~93.5 engineer-days** (Track A).
Track B adds ~52 ed when triggered.

---

## Execution principles (why the order is the order)

1. **Baseline before anything.** You cannot build on an unmerged branch.
2. **Delete before build.** Every later phase then operates on a smaller
   surface. Deleting after building means paying twice.
3. **Truth before features.** A product that displays fabricated numbers is
   worse than one that is visibly incomplete.
4. **Correctness before content.** Seeding 500 questions through a grader that
   picks the wrong method multiplies the defect by 500.
5. **Measurement before investment.** No ML decision is made without the eval
   harness that can settle it.
6. **Schema before content.** Content seeded onto the wrong schema becomes
   migration debt proportional to your success at seeding.
7. **Activation before launch, not before development.** Config is a gate, not
   a prerequisite.
8. **Performance last — but only under Track A.** With 177 log rows there is
   nothing to profile. Under Track B this inverts.

---

# M0 — Baseline Reconciliation
**Status: NOT STARTED** · 0.5 ed · 1 phase

### P0.1 — Merge N3 Phase 1a
- **Goal.** Land the completed shared-API-client work so all later phases build on one baseline.
- **Files.** Branch `m6-phase1a-fix-broken-pages` @ `3d88a8d`, plus one test-only
  addition made by the P0.1 review (`src/pages/Settings.test.tsx`) closing a
  surviving mutant. No production code changed by P0.1 itself.
- **Dependencies.** None.
- **Effort.** 0.5 ed.
- **Risk.** LOW. Verified by `git merge-tree --write-tree`: exit 0, and the
  merged tree hash **equals the branch tree hash** — so tests run on the branch
  are tests of the merge result, not a proxy for it.
- **Testing.** Frontend suite (105 after the review addition), typecheck, build.
- **Review.** PR review of the already-reviewed diff; confirm CI green on `main` after.
- **Rollback.** `git revert -m 1 <merge-sha>`.
- **Success criteria.** `/schedule`, `/notifications`, `/settings` load real API data in production.
- **DoD.** Merged, deployed, all three pages verified in browser, CI green.

**Tasks**
- **T0.1.1 Confirm Render `autoDeploy`.** *Why:* a frontend-only merge still triggers a backend redeploy; must be known, not discovered. *Outcome:* documented setting. *Files:* `render.yaml` (read). *Risk:* ephemeral disk wipe on redeploy. *Tests:* none. *Manual:* Render dashboard.
- **T0.1.2 Open and merge PR.** *Why:* the fix is finished and unshipped. *Outcome:* `main` contains Phase 1a. *Files:* none. *Risk:* none beyond deploy. *Tests:* full frontend suite. *Manual:* CI green.
- **T0.1.3 Verify live.** *Why:* the defect only manifests in the Vercel/Render origin triangle. *Outcome:* three pages confirmed. *Risk:* SPA-shell fallback returns HTTP 200 `text/html` — the bug's signature. *Manual:* DevTools Network on each page.

---

# M1 — Surface Reduction
**Status: NOT STARTED** · 5 ed · 2 phases

> Runs before all feature work. Every subsequent milestone then touches less code.

### P1.1 — Retire the un-runnable ML stack
- **Goal.** Remove deep-learning code that cannot execute on the web tier, plus its ~2 GB dependency tier.
- **Files.** `groups/engines/{gnn_engine,shap_explainer,export_onnx,mirt_engine}.py`; `groups/management/commands/retrain_ai.py`; `groups/synthetic_data_generator.py`; `models_data/*` (9 artifacts); `requirements-ml.txt`; `coding_views.py` (`USE_REAL_SHAP`, `_compute_shap_xai`); `.github/workflows/ci.yml`; `render.yaml` (`ENABLE_SHAP_XAI`).
- **Dependencies.** P0.1.
- **Effort.** 3 ed.
- **Risk.** LOW-MEDIUM. **Independent of the M6 routing measurement** — the GCN feeds explainability only (`_compute_shap_xai`, gated by `ENABLE_SHAP_XAI=false`), while routing uses sklearn + NetworkX, both core deps.
- **Testing.** Assert the heuristic XAI payload schema is byte-identical before/after. Full backend suite. CI install-time measured before/after.
- **Review.** Confirm no import of a removed module survives; confirm `_compute_xai` heuristic branch is now the only branch.
- **Rollback.** Single revert; artifacts restored from git history.
- **Success criteria.** Backend suite green; CI install time materially reduced; XAI response schema unchanged.
- **DoD.** ~872 lines and 256 KB artifacts removed; `requirements-ml.txt` gone; CI simplified; no dead imports.

**Tasks**
- **T1.1.1 Delete the four engines.** *Why:* `mirt_engine` is imported by nothing; the other three need torch, absent from the web tier. *Outcome:* `engines/` contains only what runs. *Risk:* a hidden import. *Tests:* full suite + import scan. *Manual:* none.
- **T1.1.2 Delete offline training path.** *Why:* `retrain_ai` and `synthetic_data_generator` exist only to produce artifacts nothing can load. *Outcome:* 378 lines removed. *Tests:* suite. *Manual:* none.
- **T1.1.3 Collapse the XAI branch.** *Why:* with SHAP gone, `USE_REAL_SHAP` and `_compute_shap_xai` are dead. *Outcome:* one code path. *Risk:* **response schema drift** — the frontend radar chart reads `shap_values`. *Tests:* schema-pinning test asserting key set and shape. *Manual:* open the XAI panel.
- **T1.1.4 Remove the ML dependency tier.** *Why:* ~2 GB in CI and every dev setup for unreachable code. *Outcome:* `requirements-ml.txt` deleted, CI single-file install. *Tests:* CI run. *Manual:* compare install duration.
- **T1.1.5 Drop `ENABLE_SHAP_XAI`.** *Files:* `render.yaml`, `settings.py`. *Tests:* config test.

### P1.2 — Collapse coding surfaces and remove Flashcards
- **Goal.** Three coding routes become two with distinct jobs; remove the competing spaced-repetition loop.
- **Files.** `src/components/CodingPortal.tsx` (delete); `src/App.tsx` (redirect `/code`); `src/pages/AIFlashcards.tsx` (delete); `groups/views.py` (`AIFlashcardView`); `groups/urls.py`; `AppSidebar.tsx`; `src/services/networkAccess.test.ts` (allowlist).
- **Dependencies.** P0.1.
- **Effort.** 2 ed.
- **Risk.** LOW. `CodingPortal` is the only remaining direct-axios file; deleting it shrinks the Phase 1b allowlist for free.
- **Testing.** Network guard must still pass with a 5-entry allowlist; route test for `/code` → `/coding-portal`.
- **Review.** Confirm no nav item or link points at deleted routes.
- **Rollback.** Single revert.
- **Success criteria.** Nav drops from 11 to 9 items; allowlist 6 → 5; suite green.
- **DoD.** ~590 lines removed; `/code` redirects; no dead links.

**Tasks**
- **T1.2.1 Redirect and delete `/code`.** *Why:* third door to one room; last raw-axios consumer. *Outcome:* `AdaptiveCodingPortal` is the solve surface. *Risk:* bookmarked URLs — hence a redirect, not a 404. *Tests:* routing test. *Manual:* visit `/code`.
- **T1.2.2 Remove Flashcards.** *Why:* a second spaced-repetition loop competing with the Review Queue. *Outcome:* one memory system. *Files:* page, view, URL, nav. *Tests:* suite. *Manual:* nav check.
- **T1.2.3 Shrink the Phase 1b allowlist.** *Why:* the guard fails on stale entries by design. *Outcome:* 5 entries. *Tests:* `networkAccess.test.ts`.

---

# M2 — Product Truth
**Status: NOT STARTED** · 12 ed · 4 phases

> The highest-value milestone. Every visible number is currently fabricated or
> permanently empty.

### P2.1 — Pagination correctness
- **Goal.** Stop silently truncating every list in the product to three rows.
- **Files.** `LearnLM/settings.py` (`PAGE_SIZE`); `groups/views.py` (per-view page sizes); a new guard test; `StudyGroups.tsx`, `GroupDetail.tsx`, `FileLibrary.tsx`, `AIQuiz.tsx`, `DoubtSolver.tsx`.
- **Dependencies.** P0.1.
- **Effort.** 2 ed.
- **Risk.** MEDIUM. Five frontend files use the `res.data.results || res.data` idiom — **the envelope shape must not change**, only the size.
- **Testing.** Per-view page-size assertions; guard test that no list view relies on the global default.
- **Review.** Confirm envelope unchanged.
- **Rollback.** Single revert.
- **Success criteria.** A group with 12 members displays 12.
- **DoD.** Guard test fails when `PAGE_SIZE` returns to 3.

**Tasks**
- **T2.1.1 Raise the default; set explicit per-view sizes.** *Why:* `PAGE_SIZE: 3` applies to `StudyGroupViewSet`, `MaterialViewSet`, `ListAssignedQuizView`, `getGroupMembers`. *Outcome:* sane sizes. *Risk:* payload growth on `MaterialViewSet`. *Tests:* per-view. *Manual:* a group with >3 members.
- **T2.1.2 Guard test.** *Why:* prevent silent regression. *Tests:* mutation — restore `PAGE_SIZE: 3`, guard must fail.
- **T2.1.3 Frontend paging where lists can exceed one page.** *Manual:* File Library with >20 files.

### P2.5 — Coding judge hardening and hidden-test infrastructure
- **Goal.** Make the grading verdict trustworthy before anything is awarded for it.
- **Inserted after P2.1, ahead of P2.2**, on evidence from a read-only audit rather than a preference. The roadmap did not anticipate this phase; it was numbered P2.5 rather than renumbering P2.2–P2.4, because those phases are referenced by dependency elsewhere in this document and renumbering would invalidate the references without changing any actual order.
- **Why it precedes badge awarding.** P2.2.3 awards badges for accepted submissions. Three defects made that verdict untrustworthy: the Submit response returned `expected_output` for every hidden case (with `your_output`, a submission of `print(input())` reconstructed the whole hidden suite); a learner request with no hidden tests called an LLM to invent grading truth and persisted it; and `seed_data` deleted every `Question` unguarded, cascading into `CodeSubmission`. Awarding a badge on a verdict produced by an unverified answer key does not make the gamification wrong — it makes it *credibly* wrong, and permanently, because `UserBadge` rows are not retracted.
- **Dependencies.** P0.1.
- **Risk.** MEDIUM. Touches the grading response contract and the seed commands; no learner-state locking is involved.
- **Success criteria.** No hidden grading data in any learner-facing response; no grading data created by a user request; production cannot execute a destructive reseed; every problem's coverage and oracle status is reportable.
- **DoD.** Mutation testing proves each security invariant fails when reintroduced.

**Tasks**
- **T2.5.1 Stop the answer-key leak.** *Done* — `test_results` removed from the Submit response; allowlist assertion on response fields.
- **T2.5.2 Remove request-time grading-data generation.** *Done* — plus a structural guard that the generator is unreachable from the view module.
- **T2.5.3 Environment guard on destructive seeds.** *Done* — `common/environment.py`; unset `SPARKLM_ENV` means production.
- **T2.5.4 `ReferenceSolution` model.** *Done* — no serializer, no route, no admin; structurally unable to leak.
- **T2.5.5 Hidden-test contract + validator.** *Done* — `groups/hidden_tests.py`, `validate_question_bank`.
- **T2.5.6 Oracle execution + reconciliation.** *Done* — `groups/oracle.py`, `reconcile_hidden_tests` (read-only), daily read-only validation at 17:15 UTC (22:45 IST).
- **T2.5.7 Author reference solutions and oracle-generated hidden tests.** *Blocked* — needs production DB access for the census, and per-problem output-contract review. 12 validated hidden tests is the floor, not the target.

### P2.6 — P2.12 — Trustworthy grading before trustworthy routing

**Approved reordering.** The audit proved the adaptive engine is fed by a single boolean derived from a harness that can mark correct code wrong: three reflection harnesses selected the solution method by three different rules (Python alphabetical, Java **undefined** per the JLS, JavaScript definition order), Python/JS emitted `[0,1]` where Java emitted `0 1`, Python/JS parsed JSON while the seeded data is space-separated, and all three swallowed runtime errors into stdout with exit 0.

The dependency is therefore **execution correctness → grading correctness → learner signal → mastery → routing**, and it must not be reversed: tuning routing on labels where a helper method reads as failure fits a model to corrupted data.

| Phase | Objective | Depends on |
|---|---|---|
| **P2.6** Execution Harness Correctness | Deterministic, correctly-classified execution. Versioned via `Question.execution_contract_version`; v1 is byte-identical to what shipped | P2.5 |
| **P2.7** Question Execution Contract | Extend `wrapper_contract.py` / `audit_wrapper_templates` to all five languages; add `Question.status`; a question with zero executable languages must never be servable | P2.6 |
| **P2.8** Question Factory | `reseed_questions.py` demoted to Stage-A proposal. Merge boilerplate (never replace), floor 2 → 12, validate every declared language | P2.7 |
| **P2.9** Reference Oracle + approval state | `DRAFT/UNREVIEWED/APPROVED/REJECTED`; oracle refuses anything unapproved | P2.7 |
| **P2.10** Hidden Test Generation + Mutation | Oracle-generated outputs only. Tier-1 known-wrong 100%, Tier-2 ≥80% advisory | P2.6, P2.9, census |
| **P2.11** Trusted Learner Signal | Partial credit and outcome class replace the single boolean | P2.6 |
| **P2.12** Adaptive Routing Repair | Two-sided Elo, exploration, repeat-failure exclusion, enable the curriculum gate | P2.11 |

**P2.2.2/P2.2.3 (badges), P2.3, P2.4 follow P2.12.** P2.2.1 (streaks) is independent of the judge and is already implemented (PR #13).

### P2.2 — Streaks and badges inside the transaction
- **Goal.** Make gamification actually award something.
- **Dependencies (amended).** P0.1 **and P2.5** — see P2.5's rationale. T2.2.1 (streaks) is independent of the judge and is already implemented; T2.2.2/T2.2.3 (badges) wait on P2.5.
- **Files.** `groups/services.py` (`ProgressionService.apply_submission`); `groups/models.py`; new `seed_badges` command; migration.
- **Dependencies.** P0.1.
- **Effort.** 3 ed.
- **Risk.** MEDIUM. Touches the row-locked learner-state transaction — the most concurrency-sensitive code in the repository. Must reuse the **existing** profile lock; no new locks, no new lock order.
- **Testing.** Threaded race test (pattern exists in `test_concurrency.py`); timezone-boundary tests for streaks; idempotency via existing `unique_together`.
- **Review.** Principal backend review mandatory — lock ordering is load-bearing.
- **Rollback.** Revert; migration is additive (badge rows only).
- **Success criteria.** Solving on consecutive days increments the streak; a badge appears.
- **DoD.** Concurrency test proves no double-award under parallel submissions.

**Tasks**
- **T2.2.1 Streak update under the profile lock.** *Why:* `current_streak` is read in two places and written nowhere. *Outcome:* real streaks. *Risk:* day boundaries across timezones; double-increment under concurrency. *Tests:* race + timezone. *Manual:* submit on two consecutive days.
- **T2.2.2 `seed_badges` command.** *Why:* no `Badge` rows exist, so the strip is empty even once awards work. *Tests:* idempotent re-run.
- **T2.2.3 Award rules in `apply_submission`.** *Why:* it is already the transactional home for learner state. *Risk:* rule evaluation cost inside a lock — keep it pure computation, no queries per rule. *Tests:* per-rule unit + race.

### P2.3 — Notifications producer
- **Goal.** Give the notifications system something to notify about.
- **Files.** new `groups/notifications.py` (`notify()`); `groups/services.py`; `groups/notification_views.py` (pagination); `groups/consumers.py`; `send_spaced_repetition.py`.
- **Dependencies.** P2.2 (badge events are producers).
- **Effort.** 4 ed.
- **Risk.** MEDIUM. Today `send_spaced_repetition` pushes a WebSocket event **without persisting a row**, so the live channel and the REST list disagree by construction. Fixing that is part of this phase.
- **Testing.** Producer tests per event; persistence-then-push ordering; pagination cap.
- **Review.** Confirm no notification write occurs inside the learner-state transaction (network/push must follow commit).
- **Rollback.** Revert; `Notification` rows are additive.
- **Success criteria.** Earning a badge produces both a row and a live push.
- **DoD.** REST list and WebSocket stream agree.

**Tasks**
- **T2.3.1 `notify()` service.** *Why:* one place that persists then pushes. *Risk:* pushing inside a transaction. *Tests:* ordering test.
- **T2.3.2 Wire producers** — badge earned, review due, streak at risk. **Scoped to learning events only** (social producers are frozen). *Tests:* per producer.
- **T2.3.3 Paginate `NotificationView`.** *Why:* it currently returns every row a user has ever had. *Tests:* cap assertion.
- **T2.3.4 Reconcile the spaced-repetition push.** *Why:* it pushes without persisting. *Manual:* run the command, check both surfaces.

### P2.4 — Dashboard aggregates and error boundary
- **Goal.** Every dashboard number backed by a query; no white-screen failures.
- **Files.** `common/dashboard_views.py`; `groups/views.py` (`UserDashboardStats`); `src/pages/Dashboard.tsx`; new `src/components/ErrorBoundary.tsx`; `src/App.tsx`.
- **Dependencies.** P2.2, P2.3.
- **Effort.** 3 ed.
- **Risk.** LOW.
- **Testing.** Aggregate correctness per tile; ErrorBoundary catches a thrown child.
- **Review.** Confirm every remaining tile maps to a query; confirm deleted tiles are gone from both API and UI.
- **Rollback.** Single revert.
- **Success criteria.** No hardcoded literal remains in any dashboard payload.
- **DoD.** `study_hours` tile **deleted** (it is structurally incapable of being non-zero — `update_user_activity` is a stub and `UserActivity.time_spent` is never written).

**Tasks**
- **T2.4.1 Real aggregates.** *Why:* `study_hours: 0`, `quizzes_taken: 0`, `achievement_points: 100` are literals. *Outcome:* computed from `QuizResult`, `UserBadge`, `CodeSubmission`. *Tests:* per aggregate.
- **T2.4.2 Delete uncomputable tiles.** *Why:* deleting beats displaying a zero. *Files:* also remove the false comment at `Dashboard.tsx:28` claiming the number is real.
- **T2.4.3 Surface weak topics.** *Why:* `_build_recommendation` already computes them and nothing displays them. *Outcome:* free product value.
- **T2.4.4 Route-level ErrorBoundary.** *Why:* every route is `lazy()` and one render error blanks the app. *Tests:* throwing child renders fallback.

---

# M3 — Coding Platform Integrity
**Status: NOT STARTED** · 11 ed · 3 phases

> Must precede any content seeding: a grader that picks the wrong method
> corrupts every question seeded against it.

### P3.1 — Judge0 batch grading
- **Goal.** Collapse N sequential blocking calls into one batch call.
- **Files.** `groups/coding_views.py` (`_run_on_judge0`); `groups/services.py` (`GradingService.grade`); `groups/test_coding_views.py`.
- **Dependencies.** P0.1.
- **Effort.** 4 ed.
- **Risk.** HIGH — this is the core grading path. The injected-runner test seam must be preserved (tests monkeypatch `coding_views._run_on_judge0`).
- **Testing.** Verdict parity: identical results for the same submission before/after, across all five statuses. Partial-failure behaviour.
- **Review.** Principal backend + performance review.
- **Rollback.** Feature-flag the batch path; flag off restores sequential behaviour byte-identically.
- **Success criteria.** A 10-case submission completes in one round trip.
- **DoD.** Measured latency reduction recorded in `DAILY_PROGRESS.md`.

**Tasks**
- **T3.1.1 Batch submission API.** *Why:* 10 cases = 10 serial round trips on a single thread-sensitive worker. *Risk:* Judge0 batch response ordering. *Tests:* ordering assertion.
- **T3.1.2 Partial-failure tolerance.** *Why:* today one error raises `GradingUnavailable` and discards all passed cases. *Tests:* one case errors, others still counted.
- **T3.1.3 Preserve the runner seam.** *Why:* existing tests depend on it. *Tests:* existing suite unchanged.

### P3.2 — Harness entry-point correctness
- **Goal.** Stop grading against the wrong method.
- **Files.** `groups/services.py` (three generic wrappers); `groups/models.py` (+`entry_point`); migration; backfill command.
- **Dependencies.** P3.1.
- **Effort.** 3 ed.
- **Risk.** HIGH — changes grading semantics. Existing accepted submissions were graded under the old behaviour.
- **Testing.** Multi-method `Solution` classes per language; alphabetical-ordering trap explicitly tested.
- **Review.** Principal backend review.
- **Rollback.** Revert; `entry_point` nullable so the old path remains reachable.
- **Success criteria.** A `Solution` with `helper()` and `twoSum()` calls `twoSum()`.
- **DoD.** Test proving the alphabetical bug is fixed for Python, Java and JS.

**Tasks**
- **T3.2.1 Add `Question.entry_point`.** *Why:* `dir(sol)[0]` is alphabetical; Java's `getDeclaredMethods()` order is unspecified by the JLS. *Risk:* nullable during backfill. *Tests:* migration test.
- **T3.2.2 Use it in all three harnesses, with a documented fallback.** *Tests:* per language, multi-method.
- **T3.2.3 Backfill command.** *Manual:* spot-check 20 questions.

### P3.3 — Submit/next path hygiene
- **Goal.** Idempotent submits; no unbounded queries; no LLM calls in a request.
- **Files.** `groups/coding_views.py` (`CodeSubmitView`, `NextProblemView`); `groups/serializers.py`.
- **Dependencies.** P3.1.
- **Effort.** 4 ed.
- **Risk.** MEDIUM.
- **Testing.** Duplicate-key submit produces one submission; anti-join returns identical results to the old list-based exclusion.
- **Review.** Database review of the anti-join plan.
- **Rollback.** Single revert.
- **Success criteria.** Double-click produces one grading and one Elo update.
- **DoD.** No LLM call reachable from any GET handler.

**Tasks**
- **T3.3.1 Idempotency key on submit.** *Why:* a double click currently grades and rates twice. *Tests:* concurrent duplicate submits.
- **T3.3.2 `EXISTS` anti-join in `NextProblemView`.** *Why:* it materialises every solved id into Python. *Tests:* result parity + query count.
- **T3.3.3 Remove in-request `generate_test_cases`.** *Why:* an LLM call inside a GET, with a 150 s NIM timeout behind it. *Risk:* verify unreachability first — `_servable_questions()` already excludes caseless questions. *Tests:* assert no LLM call from the next-problem path.
- **T3.3.4 Fix the arbitrary-topic fallback.** *Why:* `Topic.objects.first()` silently serves an unrelated topic. *Outcome:* explicit 404.

---

# M4 — Evaluation Foundation
**Status: NOT STARTED** · 4 ed · 1 phase

### P4.1 — Offline evaluation harness
- **Goal.** Build the thing `docs/evals/` has promised and never contained.
- **Files.** new `backend/LearnLM/evals/`; `docs/evals/`; a management command.
- **Dependencies.** P0.1 (independent of M2/M3, but sequenced here so M5–M7 can use it).
- **Effort.** 4 ed.
- **Risk.** LOW — additive, no production path touched.
- **Testing.** Harness self-tests on synthetic fixtures with known answers.
- **Review.** Principal AI/ML review of metric definitions before any result is trusted.
- **Rollback.** Delete the directory.
- **Success criteria.** A reproducible report comparing two routing policies on historical `RecommendationLog` data.
- **DoD.** `docs/evals/` contains at least one committed baseline report.

**Tasks**
- **T4.1.1 Metric definitions.** *Why:* no claim about the recommender is currently falsifiable. *Outcome:* documented, reviewed metrics.
- **T4.1.2 Replay over `RecommendationLog`.** *Why:* `predicted_success_prob` and `actual_result_correct` already exist — the flywheel was built for exactly this. *Risk:* only 177+ rows; report confidence intervals and refuse to conclude below a stated threshold.
- **T4.1.3 LLM output evals.** *Why:* an unmeasured generation pipeline produced ~1,100 unusable questions. *Outcome:* a gate M8 can use.

---

# M5 — Question Bank v2-lite
**Status: NOT STARTED** · 10 ed · 3 phases

> Design Review amendment: **v2-lite (10 ed) now, v2-full deferred.** The
> original 30-field / 10-table model serves no shipped feature. Versioning and
> review workflow return when a second content author exists.

### P5.1 — Test cases and examples as rows
- **Goal.** Replace the schema-less `hidden_test_cases` JSONField with real rows.
- **Files.** `groups/models.py` (+`TestCase`, `QuestionExample`); migrations; backfill; `services.py`; `coding_views.py` (`_sample_case`).
- **Dependencies.** P3.2.
- **Effort.** 4 ed.
- **Risk.** HIGH — touches grading input. Backfill must be provably lossless.
- **Testing.** Round-trip: every existing question grades identically before/after.
- **Review.** Database + backend review.
- **Rollback.** Keep the JSONField populated in parallel for one milestone (expand → migrate → contract).
- **Success criteria.** Grading verdicts unchanged for a sample of 50 questions.
- **DoD.** Public/hidden distinction expressible; the "answer key leak" property still holds.

**Tasks**
- **T5.1.1 Model test cases as rows with `is_public`.** *Why:* the JSONField has no schema enforcement — flagged in the code itself. *Risk:* a public case must never expose expected output for single-case questions.
- **T5.1.2 Lossless backfill.** *Tests:* per-question comparison.
- **T5.1.3 Repoint grading and `_sample_case`.** *Tests:* verdict parity.

### P5.2 — Hints, editorials, reference solutions
- **Effort.** 3 ed. **Dependencies.** P5.1.
- **Goal.** Add the teaching content, and the validation oracle.
- **Risk.** MEDIUM — reference solutions become the correctness authority.
- **Success criteria.** A question can be validated automatically end-to-end.
- **DoD.** Every reference solution passes every test case on Judge0.

**Tasks**
- **T5.2.1 `Hint` (ordered, progressive).** *Why:* the AI tutor in M7 needs a ladder to escalate.
- **T5.2.2 `Editorial` with complexity fields.** *Why:* the actual learning value.
- **T5.2.3 `ReferenceSolution` per language.** *Why:* **this is what makes automated validation possible at all.**

### P5.3 — Tags, difficulty, validation gate
- **Effort.** 3 ed. **Dependencies.** P5.2.
- **Goal.** Replace Elo-threshold difficulty labels with a real property; enable filtering.
- **Risk.** LOW-MEDIUM.
- **Success criteria.** Questions filterable by topic, tag and difficulty.
- **DoD.** A question cannot reach `approved` without passing every automated gate.

**Tasks**
- **T5.3.1 `QuestionTag` M2M + difficulty enum.** *Why:* difficulty is currently derived from a threshold at request time.
- **T5.3.2 Automated validation gate.** *Why:* ~1,100 questions entered the bank unusable. *Tests:* a knowingly-bad question is rejected.
- **T5.3.3 Question status field** (`draft`/`approved`/`retired`), replacing the `PLACEHOLDER_MARKER` string heuristic.

---

# M6 — Adaptive Learning: Measure and Operationalize
**Status: NOT STARTED** · 8 ed · 3 phases

### P6.1 — Measure the routing policy (3 ed, deps: P4.1)
Settle whether hierarchical routing beats flat Elo, using the harness. **A negative result is a valid and publishable outcome.** DoD: a committed report with confidence intervals, and a decision recorded in `DAILY_PROGRESS.md`.

### P6.2 — MLOps around the shipped classifier (3 ed, deps: P6.1)
Design Review amendment. The sklearn `RoutingClassifier` *does* run in production and has no versioning, no drift monitoring, no A/B. Add artifact versioning, input-distribution monitoring, and a policy A/B switch. **This is the ML engineering artifact that replaces the deleted GNN.**

### P6.3 — Topic uniqueness and calibration groundwork (2 ed)
Fix `Topic.name` global uniqueness → `('portal','name')`; decide nullability of `portal`; add the table that item calibration will write to.

---

# M7 — AI Quality: Code Tutoring
**Status: NOT STARTED** · 8 ed · 3 phases

### P7.1 — Prompt versioning and regression tests (3 ed, deps: P4.1)
Prompts are inline literals in `ai_services.py`, unversioned and unevaluated. Extract, version, and gate changes behind the M4 harness.

### P7.2 — In-process hint ladder (3 ed, deps: P5.2, P7.1)
Replace the n8n webhook dependency for a core learning moment with an in-process ladder over `Hint` rows, LLM fallback, then static fallback. Keeps `AgenticCoachLog`'s existing `hint_source` / `webhook_latency_ms` observability.

### P7.3 — Failure explanation from test output (2 ed, deps: P3.1)
Explain the *specific* failing case. This is where "AI Learning Platform" is earned.

---

# M8 — Content Seeding to 500
**Status: NOT STARTED** · 15 ed · 4 phases

> Must follow M5 (schema) and M3 (grader). Seeding earlier multiplies defects.

- **P8.1 Seeder pipeline on the v2-lite schema** (4 ed, deps: P5.3) — rebuild `reseed_questions` to populate rows, gated by the validation oracle.
- **P8.2 100 curated and verified** (4 ed) — 100% reference-solution-verified, human-reviewed, used to calibrate difficulty.
- **P8.3 250 with hints and editorials** (3 ed) — editorial coverage 100%; tag taxonomy frozen.
- **P8.4 500 with calibrated difficulty** (4 ed) — difficulty from observed outcomes, not seeded guesses.

**Explicitly deferred:** 1,000 and 3,000 tiers. Revisit as a business decision.

---

# M9 — Frontend Consolidation & Residual Removals
**Status: NOT STARTED** · 8 ed · 3 phases

- **P9.1 Phase 1b migration** (3 ed) — migrate the 5 remaining allowlisted files; **allowlist reaches zero**; guard then enforces the rule absolutely.
- **P9.2 React Query decision** (2 ed) — it is installed, provided, and used by zero files while 25 components hand-roll `useEffect`. Adopt on the core surfaces or remove the dependency. **Do not keep both.**
- **P9.3 Residual removals** (3 ed) — Visual Search (cannot run: `transformers` absent), Collaborative Workspace (orphaned, unlinked), Direct Chat (duplicates group chat). Also drop `pgvector` unless real retrieval is committed.

---

# M10 — Launch Readiness
**Status: NOT STARTED (GATE)** · 2 ed Track A · +52 ed if Track B triggers

### P10.1 — Infrastructure activation (2 ed)
Object storage, Auth v2, `flushexpiredtokens`, email. **Hard gate before any public or recruiter-facing launch.** Ordering within the phase is fixed: schedule token cleanup **before** enabling rotation, or blacklist tables grow unbounded.

- **Rollback.** Each item is one env var. Auth v2 rollback is proven bidirectional (`read_refresh_token` is cookie-first with a body fallback; the legacy branch reads the cookie when the body is empty).
- **DoD.** An upload survives a redeploy; the refresh cookie shows HttpOnly/Secure/SameSite=None; `localStorage` holds no refresh token.

### Track B — triggered, not scheduled
If >20 real users or any launch announcement: insert **before all remaining feature work** — capacity (paid tier, multi-worker, Redis verified in CI, load harness) · async grading · **compliance (deletion, export, retention, consent — FERPA/GDPR)** · accessibility baseline · WebSocket rate limiting + moderation + cost instrumentation · API versioning. **~52 ed.**

---

# M11 — Performance, Observability & CI
**Status: NOT STARTED** · 10 ed · 3 phases

- **P11.1 CI hardening** (3 ed) — coverage gate, frontend lint, **Redis service** (currently zero references, while production depends on the Redis channel layer), mutation testing on changed files.
- **P11.2 Retention and limits** (3 ed) — `RecommendationLog` retention/partitioning; WebSocket rate limits and payload caps; loud Redis failure instead of silent in-memory fallback.
- **P11.3 API versioning + OpenAPI** (4 ed) — `/api/v1/`, generated schema.

---

## Deferred and removed

| Item | Status | Why |
|---|---|---|
| Question Bank v2-full (versioning, review workflow, Bloom's, quality score) | **DEFERRED** | Serves no shipped feature; one content author |
| Seeding to 1,000 / 3,000 | **DEFERRED** | Business decision, not engineering |
| Belief layer, propensity logging, Thompson router, misconception fingerprints | **REMOVED** | Research-grade personalisation with no traffic to justify it |
| GNN / SHAP / ONNX / MIRT | **REMOVED** (P1.1) | Cannot execute on the web tier |
| Visual Search | **REMOVED** (P9.3) | `transformers` absent |
| Collaborative Workspace, Direct Chat, Flashcards, `/code` | **REMOVED** (P1.2, P9.3) | Orphaned or duplicative |
| Study Groups, Friends, Group Chat, Quiz, RAG, File Library, Document Processing | **FROZEN** | Work; not the thesis; security patches only |
| i18n, offline/PWA | **DEFERRED** | No repository evidence of need |
