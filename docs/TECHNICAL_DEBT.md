# SparkLM — Technical Debt Register

**Last updated:** 2026-08-06 · Every entry carries repository evidence.
Severity: **S1** production-breaking / **S2** serious / **S3** worth fixing /
**S4** cosmetic. Effort in engineer-days (ed).

---

## S1 — Live defects users are hitting right now

### D1. Every list in the product is truncated to 3 items
`LearnLM/settings.py:412` — `'PAGE_SIZE': 3` with `PageNumberPagination` as
the DRF default. It applies to `StudyGroupViewSet`, `MaterialViewSet`,
`ListAssignedQuizView` and `getGroupMembers` (`groups/views.py:115,260,844,907`).
The SPA unwraps `.results` (`StudyGroups.tsx:51`, `GroupDetail.tsx:76`,
`FileLibrary.tsx:69`) but **never follows `next`**. A group with 12 members
displays 3. A library with 40 files displays 3.
**Impact:** silent data loss in the UI across the whole app.
**Fix:** raise `PAGE_SIZE` to a sane default (50), add explicit per-view page
sizes, and make the frontend either paginate or request a large page.
**Effort:** 1.5 ed (incl. a guard test asserting no view relies on the default).

### D2. Object storage is built but not switched on
`AWS_STORAGE_BUCKET_NAME` and its four companions are read in
`settings.py:299-303` and absent from `render.yaml`. `settings.py:296-298`
documents the consequence: unconfigured ⇒ `FileSystemStorage`. Render's disk is
ephemeral, so **uploaded study materials are still destroyed on every deploy**,
which is exactly the defect M5 Phase 3 was written to fix (444 lines of tests
in `common/test_object_storage.py`).
**Impact:** permanent user data loss; RAG breaks for any material older than
the last deploy.
**Fix:** provision a bucket, set the five variables, run
`manage.py migrate_media_to_object_storage`.
**Effort:** 0.5 ed engineering + operator provisioning (owner: you).

### D3. Auth v2 is built but not switched on
`AUTH_V2_COOKIES` (`settings.py:551`) defaults to `false` and is absent from
`render.yaml`. Refresh tokens therefore still live in localStorage, reachable
by any XSS. The httpOnly-cookie path, rotation, the CSRF sentinel header and
928 lines of tests (`test_auth_v2.py`, `test_auth_v2_attacks.py`) are inert.
**Impact:** the security improvement was paid for and never delivered.
**Fix:** set `AUTH_V2_COOKIES=true` after confirming the deployed SPA build
includes the Phase 4 client. Rollback is unsetting it.
**Effort:** 0.25 ed + a staged rollout window.

---

## S2 — Hollow features: model + endpoint + UI, no producer

These are the credibility problem. Each has a database table, an API and a
rendered surface, and **nothing ever writes to it**.

### D4. Notifications have zero writers
`Notification` is defined at `groups/models.py:526`, read by
`groups/notification_views.py`, routed at `groups/urls.py:123`, and rendered by
`pages/Notifications.tsx`. Repository-wide search for `Notification.objects.create`
returns **nothing**. The page is permanently empty for every user.
`send_spaced_repetition.py:123` pushes a *WebSocket* event of type
`send_notification` but persists no row — so the live channel and the REST list
disagree by construction.
**Fix:** a `notify()` service called from submission milestones, quiz
assignment, friend requests and group invites; persist then push.
**Effort:** 3 ed. Also add pagination — `NotificationView.get` currently returns
every row a user has ever had, unbounded.

### D5. Badges are never awarded
`Badge` / `UserBadge` (`groups/models.py:555,566`) are read in exactly two
places (`coding_views.py:145`, `dashboard_views.py:69`) and written in none.
No seeder creates `Badge` rows either.
**Fix:** award rules evaluated inside `ProgressionService.apply_submission`
(already the transactional home for learner state), plus a `seed_badges` command.
**Effort:** 2.5 ed.

### D6. Streaks are never incremented
`current_streak` / `highest_streak` / `last_active_date`
(`groups/models.py:364-366`) are read at `coding_views.py:124` and
`dashboard_views.py:93`, written nowhere. Every user's streak is 0 forever.
**Fix:** update in `apply_submission` under the existing profile lock — the
correct place, and no new locking.
**Effort:** 1 ed.

### D7. Study hours can never be non-zero
`update_user_activity` (`groups/views.py:1044`) is a stub returning
`{"status":"success"}` and writing nothing; the SPA never calls it at all.
`UserActivity.time_spent` therefore stays zero, and the analytics aggregation
at `views.py:799` sums zeros.
**Fix:** decide — either implement session tracking or delete the model,
endpoint, URL and dashboard tile. Do not ship a tile that cannot be non-zero.
**Effort:** 3 ed to implement, 0.5 ed to remove.

### D8. Dashboard headline numbers are hardcoded
`common/dashboard_views.py:87-89` and `groups/views.py:91-93` return literal
`study_hours: 0`, `quizzes_taken: 0`, `achievement_points: 100`. The SPA
renders them as "Study Hours", "Quizzes Passed" and "Achievement Points"
(`Dashboard.tsx:217,260,290`), and drives the motivational message from the
constant (`Dashboard.tsx:166`) so every user sees the same one. A comment at
`Dashboard.tsx:28` asserts "the headline number is real (stats.study_hours)" —
**that comment is false**.
**Fix:** compute from `QuizResult`, `UserBadge`, `CodeSubmission`; delete what
cannot be computed.
**Effort:** 2 ed (depends on D5/D7).

---

## S2 — Scalability and correctness

### D9. Grading is N sequential blocking HTTP calls
`services.py:GradingService.grade` loops over `question.hidden_test_cases` and
calls the runner once per case. `coding_views._run_on_judge0` posts with
`wait=true` and `timeout=15` (`coding_views.py:96-100`). A 10-case question is
10 serial round trips — up to 150 s — on an ASGI worker that
`settings.py:432-437` documents as **running sync views on one
thread-sensitive worker**. This is the hard ceiling on concurrent users, and it
is why `judge0` is throttled to 10/min.
Also: any single case erroring raises `GradingUnavailable` and discards the
work already done on earlier cases.
**Fix:** Judge0's batch endpoint (`/submissions/batch`) collapses N calls to 1.
Then move grading off the request path entirely (queue + poll or webhook).
**Effort:** 2 ed for batch, 8 ed for async grading.
**Note:** `ROADMAP_V2.md` schedules async grading as **M15**, near-last. The
evidence says it is the top scaling item.

### D10. The generic harnesses pick the wrong method
`GENERIC_PYTHON_WRAPPER` (`services.py:80-81`) selects
`[m for m in dir(sol) ...][0]`. **`dir()` returns names sorted
alphabetically**, not in definition order — a `Solution` with `helper()` and
`twoSum()` calls `helper()`. `GENERIC_JAVA_WRAPPER` (`services.py:116-123`)
takes the first of `getDeclaredMethods()`, whose order the JLS explicitly does
not guarantee. The JS harness has the same shape.
**Impact:** silently wrong grading on any multi-method solution.
**Fix:** store the entry-point method name on the question; fall back to a
documented convention (`solve`) rather than a lucky sort.
**Effort:** 2 ed + a backfill.

### D11. `NextProblemView` materialises every solved id
`coding_views.py:336-346` pulls all accepted `question_id`s into a Python list,
casts each, then passes the list to `.exclude(id__in=solved_ids)`. Cost grows
without bound in the user's solve count, on a hot endpoint.
**Fix:** an `EXISTS` subquery / `~Q(...)` anti-join in SQL.
**Effort:** 0.5 ed.

### D12. LLM generation inside a GET request
`coding_views.py:458-470` calls `generate_test_cases()` — a network LLM call —
inside `GET /api/code/next/` when a served question lacks cases. The NIM
fallback path uses `timeout=150` (`ai_services.py:416`). One such request can
occupy the single worker for over two minutes.
**Fix:** generation belongs in the seeding pipeline, never in a request.
`_servable_questions()` already excludes caseless questions, so this branch is
close to unreachable — verify, then delete it.
**Effort:** 0.5 ed.

### D13. Unbounded write on a read endpoint
Every `GET /api/code/next/` inserts a `RecommendationLog` row
(`coding_views.py:446`). No retention policy, no partitioning (unlike
`CodeSubmission`). The staff dashboard then runs three unbounded `COUNT(*)`s
over it (`dashboard_views.py:109-111`).
**Fix:** retention window + partition, or aggregate counters.
**Effort:** 2 ed.

### D14. `Topic.name` is globally unique but portal-scoped
`groups/models.py:270` declares `unique=True` while `groups/models.py:264`
gives `Topic` a `portal` FK. Two portals can therefore never share a topic
name — "Arrays" in DSA blocks "Arrays" in any future portal. `portal` is also
`null=True`, so orphan topics are representable.
**Fix:** `unique_together = ('portal', 'name')`; decide whether null portal is
legal and enforce it.
**Effort:** 1 ed + migration.

### D15. Redis failure degrades silently to single-instance mode
`settings.py:581-596` defaults `CHANNEL_LAYERS` to `InMemoryChannelLayer` and
upgrades to Redis only if `REDIS_URL` is set *and* `channels_redis` imports;
the `ImportError` is swallowed with `pass`. **CI has no Redis service**
(`.github/workflows/ci.yml` contains zero `redis` references), so the
production channel layer is never exercised by any test.
**Impact:** a Redis misconfiguration looks healthy on one instance and breaks
the moment there are two.
**Fix:** fail loudly when `REDIS_URL` is set but unusable; add a Redis service
to CI.
**Effort:** 1 ed.

### D16. WebSocket messages are unthrottled
`consumers.py:82` `receive()` has no rate limit and no payload size cap; each
message writes a row (`save_message`, line 187) and fans out. Authorization is
checked only at connect (documented as deliberate at `consumers.py:256`), so a
user removed from a group keeps their socket.
**Fix:** per-connection token bucket, max payload, periodic re-authorization.
**Effort:** 2 ed.

---

## S3 — Architecture and maintainability

### D17. React Query is installed, provided, and completely unused
`App.tsx:5` creates a `QueryClient` and wraps the tree in
`QueryClientProvider`. Repository-wide, **zero** files call `useQuery` or
`useMutation`; 25 components hand-roll `useEffect` fetching instead.
**Fix:** either adopt it (this is N3 Phase 2's actual content) or remove the
dependency. Do not keep both.
**Effort:** 0.5 ed to remove; 6 ed to adopt across the app.

### D18. No error boundary anywhere
No `ErrorBoundary` or `componentDidCatch` in `studysphere-ai-11/src`. Every
route is `lazy()`-loaded; one render-time exception blanks the entire app with
no recovery path.
**Effort:** 1 ed.

### D19. Three coding surfaces
`App.tsx:129-131` routes `/coding-hub` → `CodingHub`, `/code` → `CodingPortal`,
`/coding-portal` → `AdaptiveCodingPortal`. Three entry points into one domain,
each independently maintained; `CodingPortal` is also the last raw-axios
consumer in the app.
**Fix:** pick one, redirect the others.
**Effort:** 3 ed.

### D20. The torch-dependent ML surface cannot run in production
`requirements-ml.txt` (torch, torch-geometric, onnxruntime, shap,
transformers) is deliberately **not installed on the web tier** — `render.yaml`
installs `requirements.txt` only. So the nine GNN artifacts in
`backend/LearnLM/models_data/` cannot be loaded, `ENABLE_SHAP_XAI` is `false`
in `render.yaml`, and the visual-search embedding path has no `transformers`.
Production is served entirely by the heuristic fallbacks. `docs/evals/` is
empty, so no offline evaluation backs the GNN either.
**Decision required:** either fund a worker tier that can actually run these,
or retire them. Carrying dormant ML that has never been measured against the
Elo baseline is the largest unjustified complexity in the repository.
**Effort:** 0.5 ed to retire; 10+ ed to operationalise.

### D21. "RAG" performs no retrieval
`RAGService` (`ai_services.py:300-304`) documents itself as
"Bypasses traditional FAISS" and routes **all** chunks straight into the model
context (`ai_services.py:314`). Cost and latency are linear in document size
and it will exceed the context window on large materials.
**Fix:** rename honestly, or implement real retrieval — `pgvector` and
`Document.feature_vector` already exist.
**Effort:** 4 ed for real retrieval.

### D22. Frontend test coverage is 5× thinner than backend
0.15 vs 0.79 test:source. `Dashboard.tsx` (590 lines), `AIQuiz.tsx` (611),
`GroupDetail.tsx` (690) and `AdaptiveCodingPortal.tsx` (598) have no tests.
**Effort:** ongoing; budget ~1 ed per major page.

### D23. Documentation is mostly non-operational
`docs/` is ~640 KB, of which ~400 KB is `10_INTERVIEW_HANDBOOK_PART_1..6`.
`ROADMAP_V2.md` is explicitly ordered around a job interview dated 2026-07-23
("the July 23 interview requires a live product by July 20") — that constraint
has expired, so its ordering rationale no longer holds. Two milestone numbering
schemes coexist and disagree.
**Fix:** archive the handbooks under `docs/archive/`; `ROADMAP_V2.md` is
superseded by `MASTER_ENGINEERING_ROADMAP.md`.
**Effort:** 0.5 ed.

---

## S3 — Smaller items

| ID | Item | Evidence | Effort |
|---|---|---|---|
| D24 | Bare `except:` swallows Groq client init failure | `ai_services.py:23` | 0.1 ed |
| D25 | 13 `print()` calls instead of logging | `ai_services.py` | 0.3 ed |
| D26 | `Topic.objects.first()` fallback serves an arbitrary topic when the requested one is missing | `coding_views.py:333` | 0.3 ed |
| D27 | No submit idempotency — a double click grades and rates twice | `CodeSubmitView.post` | 1 ed |
| D28 | `CodeSubmission.question` nullable; submissions can orphan | `models.py:387` | 0.5 ed |
| D29 | `google-generativeai` EOL, `PyPDF2` deprecated | `requirements.txt:33,44` | 2 ed |
| D30 | `ACCESS_TOKEN_LIFETIME` 60 min is long for an in-memory token | `settings.py:508` | 0.2 ed |
| D31 | `ALLOWED_HOSTS` defaults to `*` | `settings.py:38` | 0.1 ed |
| D32 | Email hardcoded to Gmail SMTP; creds unset in `render.yaml` ⇒ every send fails in production | `settings.py:599-604` | 0.5 ed |
| D33 | No mutation testing, coverage gate, or frontend lint in CI | `.github/workflows/ci.yml` | 2 ed |
| D34 | `migrate` runs in `startCommand`; a bad migration fails the boot with no rollback | `render.yaml` | 1 ed |
