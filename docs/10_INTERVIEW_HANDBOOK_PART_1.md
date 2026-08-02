# SparkLM Technical Interview Handbook
## Part 1 — Backend Engineering & Concurrency

**Questions 1–42 of 254**
**Companion:** Documents 01 (System Architecture) and 02 (Backend Workflow)

---

## How to Use This Handbook

You built SparkLM. That is your advantage and your exposure. An interviewer who senses a
project was assembled from tutorials will probe until it collapses; one who senses real
engineering will follow you into the details. This handbook is written for the second case.

**Three rules that will serve you better than memorising answers:**

1. **Lead with the measurement, not the technology.** "I used connection pooling" is a
   tutorial answer. "Twenty requests were opening twenty-one TCP sockets to Neon; pooling
   took it to three and cut `/healthz` p50 from 699 ms to 391 ms" is an engineering answer.
   You have real numbers. Use them.

2. **Volunteer the reversals.** You were wrong about several things — the Argon2 memory
   theory, the connection-churn refutation, the contaminated control. Saying so is not a
   weakness; it is the single strongest signal you can send that the numbers are real and
   not reverse-engineered from a blog post. Interviewers at good companies are specifically
   listening for it.

3. **Know where the bodies are.** Every answer below has a "what interviewers expect"
   section. The honest limitations are in there deliberately. If you claim SparkLM scales,
   a competent interviewer will find the single-worker ceiling in two questions and you
   will have lost the room. Get there first.

**Answer-length calibration:** aim for 45–90 seconds spoken. The "ideal answer" text below
is roughly what that sounds like. Everything after it is ammunition for follow-ups, not
part of the opening.

> **⚠ Revision notice — 2026-08-02.** An earlier revision of this handbook described the Elo as
> **two-sided** (item difficulty calibrating against the population) and stated that
> `RecommendationLog` logs **propensity** and **policy version**. Neither is true:
> `Question.base_difficulty` is a static author prior written only by seed commands, and the
> propensity columns do not exist. The claims came from reading the *planned* schema in
> `ARCHITECTURE_V2.md` §4.2 as as-built. Part 2 Q54 and Part 5 Q169–Q212 are rewritten to
> describe what the code does, with the gaps named as gaps.
>
> Two consequences for interview use. First, **do not claim self-calibrating content** — the
> schema does not support it and one follow-up will expose it. Second, this is itself a usable
> answer: asked how you keep documentation honest, the truthful reply is that you did not, once,
> and the failure had the same signature as every other in this project — plausible,
> well-formed, and wrong, with nothing objecting. It was caught by verifying the schema before
> planning Milestone 4, which is why [that plan](MILESTONE_4_PLAN.md) opens with the correction.

---

## Section A — Architecture & Framework Choices (Q1–Q12)

---

### Q1. Walk me through SparkLM's architecture.

**Ideal answer.** SparkLM is an adaptive coding-practice platform. A React SPA on Vercel
talks to a single Django backend on Render, which uses Neon Postgres with pgvector, Upstash
Redis, and three external services: Judge0 for sandboxed code execution, Groq for LLM
generation, and Gemini for embeddings. The backend is a modulith — one deployable with
strictly layered internals: views handle HTTP only, services own transactions and
orchestration, engines hold pure domain logic and never import DRF, and models handle
persistence. The interesting part is the adaptive loop: every submission updates both a
student rating and a per-question difficulty rating via Elo, and a router decides what to
serve next based on that state plus a streak-detection statistic.

**Why we chose this.** One deployable because the team is one person. The layering exists
so that the interesting logic — the adaptive engines — is testable without HTTP, a database,
or a network.

**Alternatives.** Microservices (grading service, recommendation service, content service);
a serverless function-per-endpoint design; a monolith with no internal layering.

**Tradeoffs.** The modulith accepts one blast radius per deploy in exchange for one CI
pipeline, one test suite, one mental model. Microservices were rejected explicitly:
operational cost is unjustified below roughly a ten-engineer team. Serverless was rejected
because cold starts poison WebSockets and long grading jobs, and per-invocation pricing
punishes Judge0 fan-out. The seams are preserved so extraction stays possible.

**Follow-ups.** "When would you split it?" · "What would you extract first?" · "How do you
stop the layering eroding?"

**What interviewers expect.** A clean two-minute narrative, then evidence you have thought
about the *seams*. Say the extraction order out loud: grading first (it is already behind an
injected callable), then content generation (already async-shaped, LLM-bound), then
recommendations. Mention that the frozen architecture document enforces "new code lands in
v2 modules; the legacy app shrinks by extraction and never grows" — that shows the layering
is governed, not aspirational.

---

### Q2. Why Django rather than FastAPI, Flask, or Node?

**Ideal answer.** Three reasons specific to this system. First, the ORM plus migrations
matter here because the schema is genuinely relational — a topic DAG with acyclicity
constraints, per-user mastery rows, partitioned submissions. Second, Django's auth stack
gave me a hashing framework with pluggable hashers and *transparent rehashing on login*,
which is what made the PBKDF2→Argon2 migration a no-downtime, no-reset operation. Third,
Django Admin gave me a content-operations UI for free, which matters for a project where
seeding and repairing question data is a real workflow.

**Why we chose this.** The auth stack is the decisive one. Django rehashes a user's password
automatically on next successful login when the preferred hasher changes. Rebuilding that
correctly in FastAPI would have been the largest single piece of work in Milestone 3.

**Alternatives.** FastAPI (async-native, faster on IO-bound work, better typing); Flask
(minimal); Node/NestJS (one language across the stack).

**Tradeoffs.** Django's sync-first design is exactly what caused my worst performance
problem — sync views under ASGI serialise onto one worker thread. FastAPI would not have
that issue. I traded raw concurrency ceiling for batteries that were genuinely load-bearing.
That is a defensible trade at this scale and an indefensible one at ten times the traffic.

**Follow-ups.** "So would you pick FastAPI next time?" · "What specifically does the ORM buy
you here?" · "How would you migrate?"

**What interviewers expect.** Do not say "Django is what I know." Name a capability you
actually used. The transparent-rehash answer is strong because it is specific and few
candidates know it exists. Be ready to concede the async point immediately — conceding it
before they raise it is worth more than defending it.

---

### Q3. Why ASGI and Daphne instead of WSGI and Gunicorn?

**Ideal answer.** WebSockets. SparkLM has three real-time surfaces — group chat, push
notifications, and collaborative code editing — and Django Channels requires ASGI. That is
the whole reason. It turned out to be the most expensive architectural decision in the
project, because Django's ASGI handler wraps every request in a `ThreadSensitiveContext`,
which serialises synchronous views onto a single worker thread. I measured 32 concurrent
requests through the real handler: peak overlap of one, at every concurrency level. So I got
WebSockets and paid for them with throughput.

**Why we chose this.** Real-time collaboration was a product requirement, not a nice-to-have.

**Alternatives.** Gunicorn WSGI with a separate ASGI process just for WebSockets; polling
instead of sockets; dropping real-time entirely.

**Tradeoffs.** The split-process option is the right answer at scale — WSGI workers handle
REST in parallel while a small ASGI process handles sockets. On a 512 MB free instance I
cannot afford two processes, so I took the single-ASGI-process design and its serialisation
cost.

**Follow-ups.** "How did you discover the serialisation?" · "Why not run more Daphne
workers?" · "What does the fix look like?"

**What interviewers expect.** This is one of your strongest stories because it has a
measurement, a surprise, and a considered response. Land the sequence: chose ASGI for a
product reason → measured a throughput anomaly → instrumented the real handler → found the
cause → could not fix it within the plan's constraints → constrained the rate limits instead
so the system fails safely. That arc is what senior engineering looks like.

---

### Q4. Explain your layering. How is it enforced?

**Ideal answer.** Four layers. `api/` — views, serializers, permissions, throttles — handles
HTTP and nothing else. `services/` owns orchestration and transactions. `engines/` is pure
domain logic: Elo, half-life regression, MIRT, the router. `models/` plus selectors handle
persistence. The rules are: views never mutate learner state directly, engines never import
DRF, services own transactions and locking, and external I/O is injected into services as
callables rather than imported. That last rule is what keeps the entire test suite offline —
220 tests, zero network calls.

**Why we chose this.** The engines are the intellectually valuable part. If they can only be
tested through an HTTP request against a live database and a live Judge0, they cannot be
tested meaningfully at all.

**Alternatives.** Fat views (Django's default gravity); fat models; a full hexagonal
architecture with formal ports and adapters.

**Tradeoffs.** Layering costs indirection. Reading `POST /code/submit/` end to end means
opening three files. I accepted that because the alternative — the original 200-line view
that did grading, persistence, Elo, and coaching inline — was untestable and I had already
been bitten by it.

**Follow-ups.** "What enforces 'engines never import DRF'?" · "Is it review discipline or
tooling?" · "Where has the layering leaked?"

**What interviewers expect.** Honesty on enforcement. The truthful answer is that it is
review discipline plus a frozen architecture document, **not** an import linter. Say so, and
then say what you would add: an import-boundary check in CI is about twenty lines and would
make the rule mechanical. Volunteering the gap is stronger than claiming rigour you do not
have.

---

### Q5. Why is the Judge0 runner injected into `GradingService` rather than imported?

**Ideal answer.** `GradingService.__init__` takes a `runner` callable — `Callable(source,
language, stdin) -> verdict dict`. Production passes the real Judge0 client; tests pass a
stub. This is what makes grading testable without the network, and grading is where the
subtle logic lives: output normalisation, status-code mapping, the fact that a pass requires
both matching output *and* `status_id == 3`.

**Why we chose this.** Test seam. Judge0 is metered, rate-limited, and slow. A test suite
that hits it is a test suite nobody runs.

**Alternatives.** Module-level import plus `unittest.mock.patch`; an environment flag
switching between real and fake; a recorded-cassette approach like VCR.

**Tradeoffs.** Constructor injection is explicit and honest but slightly more ceremony than
patching. Patching couples tests to import paths, which breaks whenever you move a module —
and I did move this code during the service extraction, which is exactly when patch-based
tests break.

**Follow-ups.** "Why is the runner resolved from module globals at call time in the view?" ·
"How do you test the real integration?"

**What interviewers expect.** A detail that shows care: the view resolves `_run_on_judge0`
from module globals *at request time*, specifically so the existing test seam
(monkeypatching `coding_views._run_on_judge0`) keeps working across the service boundary.
That is backward compatibility for tests, deliberately preserved during a refactor. Mention
it — it shows you refactored without breaking your safety net.

---

### Q6. Why extract `GradingService` and `ProgressionService` from the view?

**Ideal answer.** The original `CodeSubmitView.post` did everything inline: wrap the code,
call Judge0 N times, grade, open a transaction, update Elo, update mastery, apply graph
decay, and fire a coach webhook. Roughly 200 lines with a transaction in the middle and
network calls on both sides. The extraction was a *pure move* — the frozen spec required
zero test edits, so behaviour was provably unchanged — and it made the call order an explicit
contract documented in the services module rather than an accident of line order.

**Why we chose this.** The ordering *is* the correctness. Judge0 must happen before the
transaction; the coach webhook must happen after commit; the Elo farming guard must be
inside the profile lock. When all of that is inline in a view, nothing signals that reordering
two blocks introduces a race.

**Alternatives.** Leave it inline; split into more granular services; push into model methods.

**Tradeoffs.** More files, more indirection. Worth it because the transaction contract now
has a home where it can be documented and tested.

**Follow-ups.** "What is a 'pure move'?" · "How did you verify behaviour was unchanged?" ·
"Why two services rather than one?"

**What interviewers expect.** The phrase "zero test edits" is the proof. If a refactor
required changing tests, it was not a refactor — it was a rewrite with the safety net cut.
Two services because grading is stateless and injectable while progression owns the
transaction; different responsibilities, different testing strategies.

---

### Q7. How do you handle configuration and secrets?

**Ideal answer.** Twenty-three environment variables read via `os.getenv` with
development-safe defaults, loaded from `.env` locally and set in Render's dashboard in
production. `render.yaml` declares every variable but marks secrets `sync: false`, so the
blueprint is committable while the values never enter git. There is a boot guard: starting
with `DJANGO_DEBUG=false` on the well-known development `SECRET_KEY` raises
`ImproperlyConfigured` and refuses to start.

**Why we chose this.** A leaked `SECRET_KEY` simultaneously compromises sessions,
password-reset tokens, and every issued JWT, because SimpleJWT signs with it. That is three
failure domains behind one string, so shipping the dev default to production had to be
impossible rather than merely discouraged.

**Alternatives.** A secrets manager (Vault, AWS Secrets Manager); encrypted files in git
(SOPS, git-crypt); Django settings modules per environment.

**Tradeoffs.** Environment variables are simple and platform-native but have no rotation
story, no audit trail, and no versioning. At this scale that is acceptable; with a compliance
requirement it would not be.

**Follow-ups.** "How do you rotate a key?" · "What if a key leaks?" · "Why not a settings
module per environment?"

**What interviewers expect.** Be honest that rotation is manual. And you have a real story
here worth telling if the conversation allows: a full plaintext credential dump was once
shared into a working session, which is exactly how leaks happen — not through an attack,
through convenience. The response was to remove the file and rotate. Candidates who have
actually handled a credential exposure talk about it differently from candidates who have
only read about it.

---

### Q8. Why one `settings.py` rather than per-environment settings modules?

**Ideal answer.** One file, environment-driven, with `DEBUG` gating the production-only
hardening — `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER`, and
the secret-key guard. The advantage is that there is exactly one place to read to know how
the system is configured, and no chance of a setting existing in production that was never
exercised in development.

**Why we chose this.** Divergence between settings modules is a classic source of
"works locally, breaks in production."

**Alternatives.** `settings/base.py` + `dev.py` + `prod.py`; django-environ; Pydantic
settings with typed validation.

**Tradeoffs.** The single file is long and dense — it is the most decision-heavy file in the
repository. I mitigated that with substantial inline comments explaining *why* each
non-obvious value is what it is, including measurements. Someone reading the throttle block
sees the concurrency numbers that produced those limits.

**Follow-ups.** "How long is that file?" · "Would you split it now?" · "How do you validate
settings?"

**What interviewers expect.** Willingness to defend comment density. The throttle
configuration carries a table of measured concurrency behaviour in a comment, because the
numbers are the justification and separating them from the values guarantees they will drift
apart. Some interviewers dislike long comments; the counter is that these are *decision
records*, not restatements of code.

---

### Q9. Walk me through the middleware chain. Does order matter?

**Ideal answer.** CORS, Security, WhiteNoise, an access-log middleware, then Session, Common,
CSRF, Authentication, Messages, and XFrameOptions. Order matters in three places. CORS must
be first so that preflight responses get the right headers even when something downstream
short-circuits. WhiteNoise sits early because it short-circuits static requests before they
touch the rest of the stack — there is no separate web server in this topology, so Daphne
serves the admin's CSS. And the access log is placed to wrap the handler so its timing
includes everything below it.

**Why we chose this.** The access-log middleware exists because Daphne has no access log.
Without it, production requests were invisible — no method, no path, no status, no duration.

**Alternatives.** Reverse proxy for static files; structured logging via Sentry only;
platform-level request logs.

**Tradeoffs.** WhiteNoise costs a little per-request overhead on non-static paths in exchange
for removing an entire component from the topology. On this budget that is clearly right.

**Follow-ups.** "What if CORS were last?" · "Why not use nginx?" · "What does your log line
contain?"

**What interviewers expect.** Concrete failure modes. If CORS were last and an
authentication middleware rejected the request first, the browser would see a CORS error
rather than a 401 — misleading and hard to debug. That kind of answer distinguishes someone
who has debugged a middleware ordering bug from someone who has read the list.

---

### Q10. In what order does DRF authenticate, authorise, and throttle? Why does it matter?

**Ideal answer.** `APIView.dispatch()` calls `initial()`, which runs authentication, then
permissions, then throttles, before the handler. The ordering has two important
consequences. First, the throttle can see `request.user`, which is how `UserRateThrottle`
keys per-user while `AnonRateThrottle` keys per-IP. Second, and more importantly, a throttled
request pays JWT verification but not the expensive work — on `/code/submit/` a 429 costs
about 50 milliseconds instead of a multi-second Judge0 fan-out. The brake sits in front of
the cost.

**Why we chose this.** It is DRF's design, but I depend on it deliberately: the credential-
stuffing brake on `/token/` must reject before the Argon2 hash runs, or the throttle becomes
the attack rather than the defence.

**Alternatives.** Throttling at the load balancer or CDN; middleware-level throttling before
authentication; a WAF.

**Tradeoffs.** Application-level throttling still consumes a worker thread and a Redis
round-trip for a rejected request. Edge throttling would reject for free but cannot key on
user identity or scope. Ideally you have both.

**Follow-ups.** "So a 429 still costs you something?" · "Would edge throttling be better?" ·
"What does the throttle cost per request?"

**What interviewers expect.** A number: the Redis throttle read is about 50 ms of a roughly
600 ms login. And the honest admission that on a serialised worker, even rejected requests
consume the queue — which is precisely why the rate limits are also a capacity control, not
only a security control.

---

### Q11. How do you handle errors and what does your error contract look like?

**Ideal answer.** Standard HTTP semantics: 400 for validation, 401 for bad credentials, 403
for authorised-but-forbidden, 404 for missing resources, 429 for throttled, 503 for a
dependency being unavailable. The one I am careful about is 503 — when Judge0 times out,
`GradingService` raises `GradingUnavailable` and the view returns 503 with details, rather
than a 500. That distinction matters: 500 means I have a bug, 503 means someone else is down.
Conflating them makes error dashboards useless.

**Why we chose this.** Distinguishing "my fault" from "their fault" is what makes on-call
triage possible.

**Alternatives.** RFC 7807 problem-details; a uniform envelope like `{ok, data, error}`;
GraphQL-style always-200 with errors in the body.

**Tradeoffs.** My error *shape* is inconsistent and I know it. Hand-written views emit
`{"error": "..."}` while DRF serializer failures emit `{"field": ["message"]}`. The frontend
handles both, but it should not have to. A uniform envelope is on the list.

**Follow-ups.** "Show me an inconsistency." · "What status for a misconfigured question?" ·
"How does the frontend handle both shapes?"

**What interviewers expect.** Volunteer a specific defect. A question with no test cases
currently returns **500**, but that is a data-integrity problem, not a server fault — the
client can do nothing with it and it pollutes error metrics. It should be 409 or 422. Naming
your own bug before they find it converts a weakness into evidence of judgement.

---

### Q12. How is the API versioned?

**Ideal answer.** It is not. Routes are under `/api/`, not `/api/v1/`. The frozen
architecture specifies `/api/v1/*` and the implementation diverges — that is recorded as a
known divergence rather than quietly ignored. With a single first-party client that deploys
in lockstep with the backend, versioning buys nothing today; the moment there is a mobile
app or a third-party consumer, it becomes necessary.

**Why we chose this.** Speed, with the cost written down.

**Alternatives.** URL versioning; header-based versioning (`Accept: application/vnd.spark.v1+json`);
query-parameter versioning.

**Tradeoffs.** Retrofitting a version prefix means touching every route and every frontend
call — mechanical but wide. Header versioning is cleaner in theory and worse in practice for
debugging, because you cannot paste a URL into a browser and see what a client sees.

**Follow-ups.** "When does it become urgent?" · "How would you add it without breaking
clients?" · "URL or header?"

**What interviewers expect.** The migration path. Mount the existing routes under `/api/v1/`
while keeping `/api/` as an alias, move the frontend, then retire the alias. Also the sharper
point: versioning matters most for the *contract*, and the contract is already drifting —
`hiddenTestCases` is a camelCase survivor in an otherwise snake_case API, which the
architecture document calls "the cautionary fossil."

---

## Section B — Request Handling & Service Design (Q13–Q26)

---

### Q13. Trace a request through your system end to end.

**Ideal answer.** Take `POST /api/token/`. The browser hits Render's load balancer, which
appends an `X-Forwarded-For` hop. Daphne accepts it, and Django's ASGI handler opens a
`ThreadSensitiveContext` — that is where sync work queues. The middleware chain runs, URL
resolution routes to `ThrottledTokenObtainPairView`, and DRF's `dispatch` authenticates
(nothing to do for login), checks permissions (`AllowAny`), then checks the throttle, which
is a Redis read keyed on the first XFF hop with a 5-per-minute limit. The serializer looks up
the user — one indexed query — and `check_password` runs Argon2id at about 127 ms. If the
stored hash was PBKDF2, a setter fires and rehashes to Argon2 in the same request. SimpleJWT
mints the pair, the response serialises, and the access-log middleware emits one line with
the duration. Production p50 is 551 ms, of which roughly 250 ms is network.

**Why we chose this.** Nothing exotic; the value is knowing where the milliseconds go.

**Alternatives.** N/A — this is descriptive.

**Tradeoffs.** N/A.

**Follow-ups.** "Where is the time going?" · "What would you optimise first?" · "What happens
on the 6th attempt?"

**What interviewers expect.** Fluency with your own numbers, and the ability to decompose.
Server-side, that request is roughly: middleware 8 ms, DRF plus Redis 50 ms, first DB touch
109 ms, Argon2 127 ms, JWT and serialisation 20 ms. Being able to produce that breakdown
unprompted is the difference between having built something and having measured something.

---

### Q14. Your dashboard used to make five API calls and now makes one. Why?

**Ideal answer.** The dashboard fired profile, stats, groups, gamification, and a staff-only
MLOps call in parallel from the browser. On a backend where requests queue rather than
parallelise, "parallel" from the browser's perspective means five sequential trips through
the whole per-request tax on the server. I collapsed them into one
`/api/dashboard/bootstrap/` endpoint. It also removed a call that returned 403 for
essentially every user — the MLOps endpoint is staff-only, so most users were paying for a
request that could never succeed.

**Why we chose this.** The marginal cost of an additional query is about 33 ms; the marginal
cost of an additional *request* is the entire pipeline — middleware, auth, throttle, user
lookup, connection checkout. Requests are far more expensive than queries here.

**Alternatives.** HTTP/2 multiplexing (does not help — the bottleneck is the server, not the
connection); GraphQL; keeping them separate and caching aggressively.

**Tradeoffs.** The bootstrap endpoint duplicates a few small queries from two other view
modules rather than importing across them. That is a deliberate trade of DRY for
deletability — it can be removed later without touching the endpoints it mirrors, and the
module docstring says so explicitly.

**Follow-ups.** "Isn't that duplication bad?" · "Why not GraphQL?" · "How much did it save?"

**What interviewers expect.** Two things. The insight that browser parallelism is not server
parallelism — that is the non-obvious part and it lands well. And a query-shaping detail:
the groups list uses `annotate(Count("members"))` plus `.only()` rather than the full
serializer, because the frontend only calls `.length` on the member array. The original was
serialising every member of every group to produce an integer.

---

### Q15. What is an N+1 query and where did you have one?

**Ideal answer.** N+1 is when fetching N objects triggers one query per object for a related
field — one query for the list, N for the relations. I had one in the badge display, and a
subtler variant in the groups list: `StudyGroupSerializer` nests every member's fully
serialised user object, and the dashboard used that purely to compute `members.length`. So
rendering a member count materialised every member row and ran it through a serializer. The
fix was `annotate(members_count=Count("members", distinct=True))` plus `.only()` to restrict
columns.

**Why we chose this.** Aggregate in the database. Postgres counting rows is dramatically
cheaper than Django instantiating model objects and DRF serialising them.

**Alternatives.** `select_related` for forward FKs; `prefetch_related` for reverse and M2M;
denormalised counter columns.

**Tradeoffs.** `select_related` uses a JOIN — one query, wider rows. `prefetch_related` uses
a second query and joins in Python — better for many-to-many where a JOIN would multiply
rows. `annotate` is best when you only need the aggregate. Denormalised counters are fastest
to read and the easiest to get wrong; SparkLM has counter drift on CASCADE deletes as known
debt.

**Follow-ups.** "How do you detect N+1?" · "`select_related` vs `prefetch_related`?" · "Why
not a counter column?"

**What interviewers expect.** A detection method, not just a fix. `CaptureQueriesContext` in
tests, `django-debug-toolbar` locally, and query counting as an assertion. I have measured
query counts per endpoint — login is 1, dashboard bootstrap is 7, `/code/next/` is 16 — which
is how I derived the 33 ms marginal query cost in the first place.

---

### Q16. Why does `/api/code/next/` filter through `_servable_questions()`?

**Ideal answer.** Because two independent classes of broken content must never reach a
student, and both are invisible to casual inspection. The first is placeholder rows —
generated but never filled in. The second is worse: roughly 1,100 CSV-imported questions that
have a genuine description but zero test cases. They *look* seeded, so the reseed pipeline
skips them, and serving one gives the student an empty sample case followed by a guaranteed
submit failure. `_servable_questions()` excludes both at the queryset level, so every
recommendation path inherits the guarantee.

**Why we chose this.** Content safety belongs in the query layer, not in data hygiene.
Trusting that the data is clean is how you serve a broken question at three in the morning.

**Alternatives.** A boolean `is_servable` column maintained on write; validation at seed
time; a nightly cleanup job.

**Tradeoffs.** The queryset filter is always correct but costs a slightly more complex query
on every recommendation. A materialised flag is faster to query and can go stale. Given that
recommendations are already 16 queries, one more predicate is not the problem.

**Follow-ups.** "Why not fix the data?" · "How did you find the 1,100 rows?" · "What is the
performance cost?"

**What interviewers expect.** The insight that "looks seeded but is not" is the dangerous
state — a partially-populated row is more harmful than an empty one, because every automated
process skips it. That is a genuinely good content-pipeline observation and it generalises
well beyond this project.

---

### Q17. How do you validate input?

**Ideal answer.** DRF serializers at the boundary, with `raise_exception=True` so validation
failures become 400s automatically rather than being hand-checked. The important lesson came
from registration: `AUTH_PASSWORD_VALIDATORS` is configured in settings, but **DRF does not
wire it in.** Nothing calls it unless a serializer does so explicitly. Before I added
`validate_password()` to `UserSerializer`, every validator — minimum length, common-password,
complexity — was dead configuration and registration accepted literally any password.

**Why we chose this.** Serializers are the single boundary where untrusted data becomes
trusted data. Validation anywhere else is validation you can bypass.

**Alternatives.** Pydantic models; manual checks in views; database constraints only.

**Tradeoffs.** Serializer validation is per-request and in Python. Database constraints are
authoritative but produce poor error messages. I use both: `UniqueValidator` for the
friendly message, and a unique index so a race cannot slip a duplicate through.

**Follow-ups.** "How did you find that bug?" · "What are your password rules?" · "Where else
might configuration be dead?"

**What interviewers expect.** This is a strong story precisely because it is
counter-intuitive — the configuration was present, visible, and reviewed, and it did nothing.
The general lesson: **configuration that is never invoked looks identical to configuration
that works.** That is the same class of failure as the cache that accepted writes and
returned nothing. Interviewers notice when a candidate can generalise a bug into a category.

---

### Q18. Why does `CodeRunView` use the same wrapper lookup as `CodeSubmitView`?

**Ideal answer.** Because if Run and Submit disagreed about which wrapper applies, a student
could write code that passes on Run and fails on Submit with no way to diagnose why. Both
call the same alias-aware `wrapper_for(question, lang_key)`. The difference between the two
endpoints is only what they do with the result — Run executes against user-supplied stdin
and persists nothing; Submit runs the hidden cases and updates learner state.

**Why we chose this.** Consistency between the "try it" and "commit it" paths is a
correctness property in a learning product. A student debugging a phantom difference is a
student who stops trusting the platform.

**Alternatives.** Separate logic per endpoint; no wrapper on Run at all.

**Tradeoffs.** No wrapper on Run would be simpler and would let students run arbitrary
scratch code — but then Run would not reflect how the submission is actually executed, which
defeats its purpose.

**Follow-ups.** "What does `wrapper_for` do about missing wrappers?" · "Why alias-aware?"

**What interviewers expect.** The defensive detail: `wrapper_for` treats blank strings,
`None`, and non-dict values all as *absent*, because seed data is inconsistent across
generations. And `WRAPPER_LANGUAGE_ALIASES` exists because the same language acquired several
spellings over time — the same root cause as the `js`/`javascript` Judge0 bug where the
serializer validated one spelling and the language-ID map contained only the other.

---

### Q19. Why do you strip Java imports before wrapping and before storing?

**Ideal answer.** Import statements inside a wrapper class cause compile errors — Java
requires imports at the top of the file, and the wrapper puts user code inside a class body.
So I strip them with a regex before templating. The subtle part is that the *stripped* source
is also what gets persisted as `stored_code`. If I stored the original, the code in the
database would not be the code that ran, and every debugging session from that row would be
misleading.

**Why we chose this.** Stored code must be reproducible. "What did this student actually
run?" has to have a truthful answer.

**Alternatives.** Hoist imports to the top of the generated file; require students not to
write imports; use a wrapper that does not nest user code in a class.

**Tradeoffs.** Hoisting is strictly better and more work — you must parse rather than regex,
and the regex `^\s*import\s+.*?;` will mangle the word "import" inside a string literal or
comment. That is a real known limitation. Stripping is the pragmatic choice with a
documented sharp edge.

**Follow-ups.** "Does that regex have false positives?" · "What if a student needs a library?"
· "Why not hoist?"

**What interviewers expect.** Admit the regex limitation immediately — a comment containing
`import` at line start would be mangled. Then note that the `GradeResult` dataclass carries a
comment flagging `stored_code` as "a detail the extraction must not silently change," which
shows the constraint was captured where a future refactorer will see it.

---

### Q20. How does your grading decide the final status?

**Ideal answer.** Each test case must satisfy two conditions to pass: normalised output
equality *and* Judge0 `status_id == 3` (Accepted). Matching output from a crashed process is
not a pass. Then the final status is derived by scanning all case results in priority order:
`status_id` 5 is time-limit, 6 is compile error, 7 through 12 are runtime errors, and only
if none of those appear does it fall through to accepted-or-wrong-answer. The critical
implementation detail is that `status_id` must be carried into each per-case result
dictionary — without it, the scan finds nothing and every failure collapses to
"wrong answer," so students see "wrong answer" for compile errors and timeouts.

**Why we chose this.** A learning platform that reports a compile error as a wrong answer is
actively teaching the wrong lesson. Feedback quality is the product.

**Alternatives.** Trust Judge0's status alone; compare raw output without normalisation;
report only pass/fail.

**Tradeoffs.** Normalisation — line endings, trailing whitespace — makes grading forgiving,
which is right for a learning tool and wrong for a competition judge where exact output is
part of the problem.

**Follow-ups.** "Why normalise?" · "What is status_id 11?" · "How did you find the collapse
bug?"

**What interviewers expect.** The insight that the *absence* of a field caused a silent
degradation in feedback quality rather than an error. Nothing crashed; students just got
worse information. Those are the hardest bugs to find because nothing reports them, and
saying that out loud shows real production instinct.

---

### Q21. What are your boot probes and why do they exist?

**Ideal answer.** Two advisory checks in `CommonConfig.ready()`. The first writes a sentinel
to the cache and reads it back — because production once ran with a cache that accepted
writes and returned nothing, which silently disabled all rate limiting, the curriculum DAG
cache, and the leaderboard cache. Nothing errored and the test suite stayed green, because
tests use LocMemCache, which works. The second verifies the password hashers can be
constructed and that Argon2 is present — because removing that hasher locks out every
migrated user while Django reports ordinary failed logins. Both are advisory: they log an
actionable ERROR and never raise.

**Why we chose this.** Both failures are *completely silent*. A boot probe is the cheapest
way to convert a silent misconfiguration into one line in the deploy log.

**Alternatives.** Fail fast and refuse to boot; health-check endpoints that verify
dependencies; external synthetic monitoring.

**Tradeoffs.** Refusing to boot is tempting and wrong here. A service that will not start
helps nobody, and the operator may be mid-rollback — during a deliberate hasher rollback,
the "wrong" configuration is the intended one. So the hasher probe logs a WARNING for a
supported rollback and an ERROR only for the genuinely broken case.

**Follow-ups.** "Why not fail fast?" · "How do you test a boot probe?" · "What would you add
next?"

**What interviewers expect.** The distinction between a *misconfiguration* and a *deliberate
rollback*, and the fact that the probe distinguishes them. That is unusually thoughtful for
a health check, and it is testable — I assert that a supported rollback logs exactly one
warning and zero errors.

---

### Q22. How do you make operational tasks repeatable?

**Ideal answer.** Sixteen Django management commands. The load-bearing ones are
`ensure_submission_partitions`, which maintains the partition horizon and runs in the deploy
chain; `password_hash_status`, which reports migration progress; and
`audit_wrapper_templates`, a read-only compatibility check against production data. The
principle is that anything I would otherwise do by pasting code into `manage.py shell`
becomes a command, because a shell snippet is untested, unversioned, and subtly wrong in ways
nobody reviews.

**Why we chose this.** A concrete example: the deployment runbook used to tell operators to
paste a seven-line snippet to check migration progress. That snippet mislabelled Google SSO
accounts and could not detect an unreadable hash — the exact condition meaning users are
locked out right now. The command handles both and exits non-zero on the lockout case.

**Alternatives.** Shell snippets in documentation; a Django admin action; an external script.

**Tradeoffs.** Commands are testable and versioned but cost more upfront than a snippet. The
tipping point is whether the logic has *judgement* in it — counting rows does not deserve a
command; deciding which rows count toward a migration denominator does.

**Follow-ups.** "What does exit code 2 mean?" · "How do you test a management command?" ·
"What is not a command that should be?"

**What interviewers expect.** The self-critical part: I initially added a
`--fail-if-incomplete` flag for a scheduled check that does not exist, then removed it in
review as speculative. Being able to point at something you deleted from your own work is
worth more than anything you added.

---

### Q23. What is idempotency and where does it matter in SparkLM?

**Ideal answer.** An idempotent operation produces the same result whether applied once or
many times. `ensure_submission_partitions` is idempotent by design — it can run on every
deploy, and it is self-healing after downtime because it also relocates rows that landed in
the default partition. Submissions are deliberately *not* idempotent: each POST creates a new
`CodeSubmission`, because resubmitting the same code is a real, meaningful learning event.
But the *rating* effect is idempotent-ish, via the Elo farming guard: re-solving an already-
accepted problem updates mastery and half-life but awards zero rating change.

**Why we chose this.** Distinguishing "this action is disallowed" from "this action is
allowed but does not score" is the right framing for a learning product. Blocking re-solves
would break spaced repetition; letting them farm rating would break the ranking.

**Alternatives.** Idempotency keys on submission; blocking duplicate submissions; allowing
unlimited rating gain.

**Tradeoffs.** No idempotency key means a double-clicked submit button creates two rows and
two Judge0 fan-outs. The farming guard means at most one of them scores, so the damage is
cost rather than correctness.

**Follow-ups.** "What if the client retries on timeout?" · "Would you add an idempotency
key?" · "Where is the guard enforced?"

**What interviewers expect.** The concurrency detail: the farming guard sits *inside* the
profile row lock. Outside it, two simultaneous first-solves could both read "not yet solved"
and both award rating. That is the difference between a guard and a race with a guard-shaped
comment.

---

### Q24. How do you handle a slow or failing third-party dependency?

**Ideal answer.** Each dependency has an explicit failure behaviour, and the rule is *fail
the feature, not the request — and never fail silently*. Judge0 has a 15-second timeout;
timeouts and transport errors become `GradingUnavailable`, which the view maps to 503 with
details. Groq falls back to NVIDIA NIM, but **only on daily-quota errors** — falling back on
any error would mask transient faults and double every failure's latency. Gemini being
unavailable degrades document search rather than breaking it. The agentic coach falls back to
a static hint keyed on failed-attempt count. Sentry no-ops without a DSN.

**Why we chose this.** Judge0 is the one dependency with no fallback, because grading without
execution is meaningless — so it says so loudly rather than pretending.

**Alternatives.** Circuit breakers; retry with exponential backoff; queue and process
asynchronously.

**Tradeoffs.** No circuit breaker means a persistently-down Judge0 costs 15 seconds per
request, and on a serialised worker that is catastrophic — 10 queued submissions is 150
seconds. A circuit breaker is the correct next addition and I would name it as such.

**Follow-ups.** "Why not retry Judge0?" · "What about a circuit breaker?" · "Why is the NIM
fallback so narrow?"

**What interviewers expect.** The narrow-fallback reasoning is the strongest part. Most
candidates describe fallbacks as universally good. Explaining that a *broad* fallback is
harmful — it hides transient faults and doubles latency on every failure — shows you have
thought about the failure taxonomy rather than pattern-matching to "add resilience."

---

### Q25. Why is your health check hitting the database?

**Ideal answer.** Because a health check that only proves Python is running is a health check
that lies. `/healthz` executes `SELECT 1` and returns 503 if it raises. It is Render's
configured `healthCheckPath`, so if the database is unreachable the platform knows the
instance is not serving. The side effect is that pinging it also keeps Neon awake and
consumes Neon's compute budget — an accepted trade, because a Neon wake costs a few seconds
and a Render cold start was measured at 92.9 seconds.

**Why we chose this.** The dependency that actually breaks is the database, so that is what
the check must exercise.

**Alternatives.** Liveness versus readiness split; a static 200; checking every dependency
including Redis and Judge0.

**Tradeoffs.** Checking *every* dependency makes the health check itself fragile — a Judge0
outage would mark the instance unhealthy and take the whole app down when most of it still
works. Database-only is the right granularity: without Postgres, essentially nothing works.

**Follow-ups.** "Liveness vs readiness?" · "What if Redis is down?" · "Isn't this a DDoS
amplifier?"

**What interviewers expect.** The explicit reasoning about scope — why the database and
*only* the database. Also note that both health endpoints have throttling explicitly disabled
(`throttle_classes = []`), because a throttled health check reports the service down under
exactly the load where you most need the truth.

---

### Q26. How do you know what is happening in production?

**Ideal answer.** Honestly, less than I would like, and I can tell you exactly where the gap
is. I have Sentry for unhandled exceptions, one access-log line per request with method, path,
status and duration, boot probes that surface silent misconfiguration, and a database-backed
health endpoint. What I do not have is in-container memory or CPU visibility. Every
memory conclusion in this project is inference from external symptoms — I could not read
resident set size from inside the container, so I reasoned from latency curves and failure
thresholds instead. Render's metrics dashboard would answer in one screenshot what took me
two rounds of review to infer.

**Why we chose this.** The free tier has no metrics API, and adding a metrics agent to a
512 MB instance costs the very resource I would be measuring.

**Alternatives.** Prometheus plus Grafana; APM (Datadog, New Relic); a `/metrics` endpoint;
structured JSON logs into a log aggregator.

**Tradeoffs.** Every one of those costs memory or money. On this budget I chose targeted
probes over general observability, which works until you have a question the probes were not
designed to answer — which is precisely what happened with the memory investigation.

**Follow-ups.** "How did you debug the outage then?" · "What would you add first?" · "What
does 'inference from external symptoms' mean?"

**What interviewers expect.** This is the most impressive answer in the set if you deliver
it correctly, because you are volunteering the weakest part of your system and demonstrating
that you know the difference between what you measured and what you inferred. Say plainly:
"I never proved it was memory. I proved the service failed at 40 concurrent auth requests and
that a hash-free endpoint at the same concurrency did not. That is strong evidence, not
proof, and I labelled it as a hypothesis." Interviewers at strong companies weight
epistemic honesty very highly.

---

## Section C — Concurrency (Q27–Q42)

---

### Q27. Explain how concurrency works in your backend.

**Ideal answer.** It largely does not, and that is the headline finding of the project.
Django's ASGI handler wraps each request in `async with ThreadSensitiveContext()`, and
synchronous views execute on a thread-sensitive worker. I instrumented the real handler with
a sleeping view recording thread identity and interval overlap: at 1, 2, 4, 8, 16, and 32
concurrent requests, peak overlap was **one** every time, and wall clock scaled perfectly
linearly. Production agrees — 32 concurrent `/healthz` requests take 3.88 seconds against
0.76 for one. So throughput is capped at 1/W where W is per-request server time, regardless
of arrival rate.

**Why we chose this.** It was not chosen; it is a consequence of running sync Django views
under ASGI, which was itself required for WebSockets.

**Alternatives.** Async views throughout; multiple worker processes; Gunicorn with threads.

**Tradeoffs.** Async views would require rewriting every database call — Django's ORM is
sync-first and `sync_to_async` reintroduces the same thread-sensitivity. Multiple processes
need memory I do not have: 4 workers × roughly 202 MB resident exceeds 512 MB before any
request arrives.

**Follow-ups.** "How did you measure that?" · "Why not more workers?" · "What is W for a
login?"

**What interviewers expect.** The measurement method, and one caveat you should volunteer: a
later test against real Daphne showed 12 sequential requests landing on 12 *distinct* thread
ids, which is in tension with the single-thread conclusion. Both observations are recorded;
the production scaling curve is the stronger evidence for queueing behaviour, and resolving
the discrepancy is open work. Presenting an unresolved contradiction in your own data,
labelled as such, is far stronger than presenting a tidy story.

---

### Q28. How did you discover the serialisation?

**Ideal answer.** Backwards, from a production incident. Forty concurrent authentication
requests returned 502 for 32 of them and took the service down for about a minute. My first
hypothesis was memory — Argon2 at 19 MiB per hash times 40 concurrent is 760 MiB against a
512 MB limit, which is a tidy story. I tested it with a barrier-synchronised experiment and
confirmed the memory does scale linearly at 19.1 MiB per hash. But then I ran a clean control:
40 concurrent requests to a hash-free endpoint completed in 3.1 seconds with zero failures.
So the instance handles 40 concurrent connections fine. That killed the memory theory and
sent me to the threading model, where the sleeping-view experiment showed peak overlap of one.

**Why we chose this.** Controls. My earlier control had been contaminated — I ran it
immediately after the auth burst while the service was still saturated, which inflated it and
made Argon2 look guilty.

**Alternatives.** Reading the framework source first; profiling; asking someone.

**Tradeoffs.** Measurement-first cost me a production outage. Source-reading-first would have
been cheaper and I would not have believed it without the numbers.

**Follow-ups.** "So Argon2 was innocent?" · "What did the contaminated control cost you?" ·
"Would you run that test again?"

**What interviewers expect.** This is your best story. It has a wrong hypothesis, a
disciplined test that *confirmed* the mechanism, a control that *invalidated the conclusion
anyway*, and a correction to the record. The lesson to state explicitly: **confirming a
mechanism is not the same as confirming it is the cause.** Argon2 really does allocate
19.1 MiB per hash linearly — that was true and irrelevant, because the requests never overlap.

---

### Q29. Why does `ProgressionService` use `select_for_update`?

**Ideal answer.** Because two concurrent submissions from the same user would otherwise
race on the profile row — both read Elo 1200, both compute a new rating from 1200, and one
write is lost. `select_for_update` takes a row-level write lock so the second transaction
blocks until the first commits. The profile row is the per-user serialisation point: locking
it first means everything downstream in that transaction is safe from concurrent
modification by another submission from the same user.

**Why we chose this.** Pessimistic locking is right here because contention is genuinely
likely — a student double-clicking submit, or a fast solve while a previous one is still
grading — and the critical section is short.

**Alternatives.** Optimistic concurrency with a version column and retry; `F()` expressions
for atomic increments; serialisable isolation.

**Tradeoffs.** `F()` expressions are atomic and lock-free but only work for arithmetic on a
single column — Elo needs a read, a computation involving the question's difficulty, and a
conditional farming check, so it cannot be expressed as an increment. Optimistic locking
would avoid blocking but needs retry logic, and under a double-click the retry rate would be
high.

**Follow-ups.** "Why not `F()`?" · "What is the isolation level?" · "How long is the lock
held?"

**What interviewers expect.** The reason `F()` does not work — that is the discriminating
question. Also that lock duration is bounded because all network calls are outside the
transaction; the critical section is database work only.

---

### Q30. Why do you lock mastery rows in ascending topic ID order?

**Ideal answer.** Deadlock preclusion. A submission may touch several mastery rows — the
question's own topic plus any downstream topics affected by graph decay. If transaction A
locks topic 5 then topic 9, and transaction B locks 9 then 5, they deadlock. Postgres will
detect it and kill one, so the user sees a random failure. Sorting the topic IDs and locking
in ascending order means every transaction in the system acquires these locks in the same
sequence, which makes a cycle impossible.

**Why we chose this.** Consistent global lock ordering is the standard solution and it is
free — it costs a `sorted()` call.

**Alternatives.** Lock a single coarse row (the profile) and nothing else; retry on deadlock;
`NOWAIT` with application-level backoff.

**Tradeoffs.** Coarser locking reduces concurrency between different users. Retry-on-deadlock
works but converts a design problem into a runtime cost, and deadlock retries are hard to
test. Ordering is deterministic and testable.

**Follow-ups.** "What if a new code path locks in a different order?" · "How do you test
this?" · "Does Postgres detect deadlocks?"

**What interviewers expect.** Awareness that the invariant is fragile — it holds only as long
as *every* code path respects it, and nothing mechanically enforces that. It is documented in
the service module and in the frozen architecture as "lock order: profile → mastery, topic-id
ascending." Naming the enforcement gap is better than claiming safety you cannot prove.

---

### Q31. Why are the Judge0 calls outside the transaction?

**Ideal answer.** Because holding a row lock across a multi-second HTTP call to a third party
would serialise part of the user base behind the slowest external request. A submission can
make N Judge0 calls at up to 15 seconds each; if that happened inside the transaction, the
profile row would be locked for potentially minutes, and every other operation touching that
user would block. Worse, a Judge0 hang would hold a database connection from a pool of ten.
So grading completes first, then the transaction opens, does pure database work, and commits.
The coach webhook fires strictly after commit for the same reason.

**Why we chose this.** "No network calls inside a transaction" is one of the few rules in the
frozen architecture stated as an absolute.

**Alternatives.** Grade inside the transaction; use a shorter timeout; queue grading
asynchronously.

**Tradeoffs.** Grading outside the transaction means a crash between grading and persistence
loses the result — the student's code ran and the outcome vanished. That is the accepted cost.
Async grading via Celery is the architected future state: persist PENDING, enqueue, push the
result over WebSocket.

**Follow-ups.** "What if the process dies after grading?" · "How would async grading change
this?" · "What is the pool size?"

**What interviewers expect.** The connection-pool angle. `max_size=10`, so ten hung Judge0
calls inside transactions would exhaust the pool and take the entire application down, not
just grading. Connecting a transaction-scope decision to a pool-exhaustion failure mode shows
systems thinking.

---

### Q32. What is the Elo farming guard and why is its placement critical?

**Ideal answer.** Re-solving an already-accepted problem must not award rating — otherwise a
student farms points by resubmitting one easy solution. The guard queries whether an accepted
submission already exists for this user and question. The critical part is that it runs
**inside** the profile row lock. Outside the lock, two concurrent first-solves would both
read "not yet solved" and both award rating. Inside it, the second transaction blocks until
the first commits, so it sees the accepted row and correctly awards zero.

**Why we chose this.** It is a read-then-act pattern, and every read-then-act is a race unless
something serialises it. The lock we already hold does that for free.

**Alternatives.** A unique constraint on (user, question, accepted); application-level dedupe;
accept the race as low-impact.

**Tradeoffs.** A partial unique index would be authoritative and database-enforced, but
submissions are partitioned, and Postgres requires the partition key inside every unique
constraint — so the natural constraint is not available. The lock-based guard was the
pragmatic route.

**Follow-ups.** "Why not a unique constraint?" · "What does the user see?" · "How would you
test the race?"

**What interviewers expect.** The partitioning interaction is a genuinely sophisticated
point — a schema decision made for data lifecycle reasons removed a concurrency primitive you
would otherwise reach for. Also mention the UX: re-solving still updates mastery and half-life
and returns an explicit message that repeat solves keep memory fresh but do not change rating.
The behaviour is explained, not silently ignored.

---

### Q33. What is a savepoint and where do you use one?

**Ideal answer.** A savepoint is a nested rollback point inside a transaction — in Django,
`transaction.atomic()` nested inside another `atomic()` block. I use one for graph-decay
cross-pollination, which propagates a small mastery penalty to downstream topics when a
student fails. That is best-effort enrichment: if it fails, I want to lose the decay, not the
submission. The savepoint means an exception there rolls back only the decay and the outer
transaction still commits the submission and the Elo update.

**Why we chose this.** Correctly classifying work as essential versus enrichment, and giving
each the failure semantics it deserves.

**Alternatives.** Let it fail the whole transaction; do it after commit in a separate
transaction; move it to a background job.

**Tradeoffs.** After-commit would be simpler but introduces a window where mastery is
inconsistent. A background job is the cleanest and requires infrastructure I do not have.

**Follow-ups.** "What is the cost of a savepoint?" · "Why is decay best-effort?" · "What do
you log?"

**What interviewers expect.** The classification instinct. Most candidates treat a transaction
as all-or-nothing; recognising that a single logical operation contains work at *different
criticality levels*, and expressing that with savepoints, is a step up. The failure also logs
with `logger.exception` including user and topic — silent best-effort is just silent.

---

### Q34. How would you scale this backend to 10,000 concurrent users?

**Ideal answer.** Nothing in the current deployment survives that, and the ordering of fixes
matters. First, get off the free tier — 0.1 shared vCPU is 6.8× slower than commodity
hardware, so every CPU-bound millisecond is multiplied. Second, break the serialisation: run
Gunicorn with uvicorn workers, N processes, which requires the memory a paid instance
provides. Third, add admission control — cap in-flight requests and shed with 503 and
`Retry-After` rather than queueing until the load balancer returns 502. Fourth, move grading
to Celery so a submission returns 202 immediately and the result arrives over WebSocket.
Then horizontal scaling becomes possible, because the app is already stateless — sessions are
JWTs, cache and channel layer are in Redis.

**Why we chose this.** The order is by constraint-removal, not by appeal. Adding workers
before adding memory just OOMs faster.

**Alternatives.** Vertical scaling only; serverless; a full rewrite in an async framework.

**Tradeoffs.** Async grading changes the client contract from synchronous to eventual, which
is real frontend work. Admission control converts some slow requests into fast failures,
which is better but is a visible behaviour change.

**Follow-ups.** "What breaks first?" · "Is your app stateless?" · "What about the database?"

**What interviewers expect.** A named first bottleneck with a number: the current ceiling is
roughly 20 concurrent, measured — 10 concurrent completes in 7.1 seconds with zero failures,
20 in 103 seconds with zero failures, 40 fails. Also that the *rate limits* are currently set
below capacity deliberately, so the system degrades rather than collapses — and that this is
a stopgap, not scaling.

---

### Q35. What is admission control and why do you not have it?

**Ideal answer.** Admission control caps how many requests are in flight and rejects the
excess immediately — a fast 503 with `Retry-After` rather than an unbounded queue. SparkLM has
none, at any layer. Daphne has no concurrency flag, and asgiref 3.11 has no thread-count knob.
So the queue is unbounded, and overload manifests as 502s from the load balancer after
requests have already consumed 20-plus seconds each. That is the worst possible failure mode:
the work is done and then discarded.

**Why we chose this.** It was not chosen. It is a gap I identified while reviewing my own
connection-pooling work and rated HIGH.

**Alternatives.** A semaphore in middleware; edge-level concurrency limits; a queue with a
bounded depth and fast rejection.

**Tradeoffs.** A middleware semaphore is roughly twenty lines and would convert 502s into
503s — strictly better, since a client can retry a 503 sensibly. The risk is choosing the
limit: too low wastes capacity, too high does nothing.

**Follow-ups.** "How would you implement it?" · "What limit would you pick?" · "Why is 503
better than 502?"

**What interviewers expect.** The limit derived from measurement, not guessed: 20 concurrent
completes with zero failures, so that is the number, and it would need re-deriving on any
instance change. Also the connection between this gap and connection pooling — pooling
introduced a *new* failure mode where demand above `max_size` can raise `PoolTimeout`, turning
overload into 500s. Admission control is now more valuable than before, and I flagged that in
my own review.

---

### Q36. How do you test concurrency?

**Ideal answer.** Three levels. Unit-level, with real threads: I spawn 25 concurrent queries
against a pool sized 10 and assert all succeed, the pool never exceeds `max_size`, and no
`PoolTimeout` is raised. Integration-level, with `transaction=True` tests that exercise
`select_for_update` paths. And production load tests at 5, 10, 20, and 40 concurrent with a
clean control against a hash-free endpoint at the same concurrency. The critical lesson is
about harnesses: my first connection-churn test used Django's `AsyncClient`, concluded
connections were reused, and was **wrong** — `AsyncClient` reuses a single thread and
structurally cannot reproduce a per-thread defect.

**Why we chose this.** Because a concurrency test that cannot exhibit the concurrency bug is
worse than no test — it produces false confidence.

**Alternatives.** Locust or k6 for load; `pytest-xdist`; chaos testing.

**Tradeoffs.** Real-thread tests are slower and can be flaky. Production load tests are the
only fully honest measurement and they cost you an outage if you push too far — which I did.

**Follow-ups.** "How did you catch the harness problem?" · "Are thread tests flaky?" · "What
does a barrier do?"

**What interviewers expect.** The `AsyncClient` story, told as a methodology failure rather
than an oversight. The fix is documented in the test module so the next person does not repeat
it: reproducing the defect requires real Daphne or explicit threads. Also the barrier
technique — without one, fast threads finish before slow ones start and you measure nothing.

---

### Q37. What is thread-local state and how did it bite you?

**Ideal answer.** Thread-local state is data scoped to one thread rather than the process.
`django.db.connections` is thread-critical — implemented with `asgiref.local.Local(thread_critical=True)`.
Combined with `ThreadSensitiveContext` minting a fresh executor thread per request, that means
each request began with *no* database connection and dialled Neon again: a TCP plus TLS plus
Postgres startup handshake worth roughly 7.2 network round-trips. `CONN_MAX_AGE=60` was
configured and had a comment claiming it let each worker thread reuse a connection. It never
applied, because the thread does not outlive the request.

**Why we chose this.** Nobody chose it — it is an interaction between two independently
reasonable designs.

**Alternatives.** A process-wide connection pool (what I did); external pooling only; async
connections.

**Tradeoffs.** The fix is `OPTIONS['pool']` with psycopg3, which works because the pool lives
in `DatabaseWrapper._connection_pools`, a **class** attribute shared by every per-thread
wrapper. It outlives the threads that thread-local connections cannot. It requires
`CONN_MAX_AGE=0` — Django refuses to combine pooling with persistent connections.

**Follow-ups.** "How did you prove it?" · "Why does a class attribute fix it?" · "What did it
save?"

**What interviewers expect.** Measured evidence: 12 requests produced 12 connections on 12
distinct thread IDs, and socket counting showed 20 requests opening 21 TCP sockets. After
pooling, 25 requests reuse 3. Production `/healthz` p50 went 699 ms to 391 ms; the database
stage alone went 421 ms to 109 ms. Also the meta-point — the setting was configured, commented,
and reviewed, and it did nothing. Same category as the dead password validators.

---

### Q38. Why is connection health checking mandatory with your pool?

**Ideal answer.** Neon's pooler drops idle connections and free-tier compute auto-suspends,
so a pooled connection can be dead by the time it is handed out. With `CONN_HEALTH_CHECKS`
disabled I observed this twice in testing: the log shows
`WARNING pool discarding closed connection: <Connection [BAD]>` immediately followed by
`ERROR Service Unavailable: /healthz`. The pool discards the dead connection only *after* the
request has already failed. With the setting enabled, Django wires psycopg_pool's checkout
validation and the dead connection is replaced before the query runs.

**Why we chose this.** Correctness over latency. The check costs exactly one round-trip; the
handshake it replaces costs about 7.2.

**Alternatives.** No validation and retry on failure; `max_idle` tuned below the provider's
idle timeout; a keepalive query.

**Tradeoffs.** The check is a per-checkout cost on every request. I also set `max_idle=300`
to recycle connections before Neon's roughly five-minute suspend window, which reduces how
often the check finds a dead connection — but does not remove the need for it, because
suspension can happen at any time.

**Follow-ups.** "How much does the check cost?" · "Why not just retry?" · "What is
`max_lifetime` for?"

**What interviewers expect.** That you learned this from an observed failure rather than
documentation, and that you can quote the log line. Retry-on-failure sounds equivalent but is
not: the request has already failed by then, so you would be retrying at the application layer
with all the idempotency questions that raises.

---

### Q39. What is the difference between concurrency and parallelism here?

**Ideal answer.** Concurrency is handling many requests in overlapping time periods;
parallelism is executing them simultaneously on different cores. SparkLM has concurrency —
Daphne accepts many connections at once, and the event loop interleaves I/O — but essentially
no parallelism for synchronous view code, because it serialises onto a thread-sensitive
worker, and because the instance has 0.1 of a shared vCPU anyway. Even with perfect
threading, one-tenth of a core cannot run two CPU-bound Argon2 hashes in parallel.

**Why we chose this.** Worth separating because the fixes differ. More workers buy parallelism
and need memory; async views buy concurrency for I/O-bound work and need a rewrite.

**Alternatives.** N/A — conceptual.

**Tradeoffs.** For an I/O-bound workload, concurrency alone would be enough. Mine is mixed:
Judge0 and LLM calls are I/O-bound, but Argon2 and serialisation are CPU-bound, and the CPU
is the scarcest resource at 6.8× slower than commodity.

**Follow-ups.** "Which of your endpoints are CPU-bound?" · "Would async help?" · "How did you
measure 6.8×?"

**What interviewers expect.** The calibration method, because it is a nice trick: I derived
the CPU factor from a *known* workload change. Swapping PBKDF2 at 1,000,000 iterations for
Argon2 cut login by 2,120 ms in production; the same swap locally is 312 ms. The ratio gives
6.8×, and that predicts Argon2 on Render at about 127 ms — which independently matched the
figure derived from the endpoint ladder. Two methods agreeing is what makes it trustworthy.

---

### Q40. What happens when two users submit at the same time?

**Ideal answer.** Nothing contends. The locks are per-user: each transaction locks *its own*
profile row and *its own* mastery rows, so two different users never block each other on
learner state. What they do contend for is the shared resources — the single worker thread,
the ten-connection pool, and the Judge0 rate limit. So the interaction is queueing, not
locking. That is an important distinction because they have different fixes: lock contention
is solved by finer-grained locking, queue contention by more workers.

**Why we chose this.** Per-user locking is the natural granularity because learner state is
per-user by construction.

**Alternatives.** Global lock (catastrophic); optimistic concurrency; table-level locking.

**Tradeoffs.** Per-user locks give maximum concurrency for the state, which is then wasted by
the serialised worker. Fixing the worker would immediately make the locking granularity pay
off — the design is ready for a scaling fix that has not happened.

**Follow-ups.** "So the locking is over-engineered?" · "What about the same user twice?" ·
"Which resource saturates first?"

**What interviewers expect.** Push back gently on "over-engineered." The locking is correct
for the multi-worker deployment the architecture targets, and correctness under concurrency is
not something you retrofit after an incident. But do concede the honest version: today, the
worker thread saturates long before lock contention would.

---

### Q41. How do WebSockets change your concurrency story?

**Ideal answer.** WebSockets are the reason for ASGI, and they behave differently from
requests: they are long-lived, mostly idle, and genuinely async. `GroupChatConsumer` is an
`AsyncWebsocketConsumer`, so the socket handling itself does not consume the sync worker —
only the database calls inside it do, wrapped for the async context. Authorisation happens
once at `connect()`, before `accept()`: membership is verified and a non-member's socket is
closed rather than joined, so there is no per-message permission cost.

**Why we chose this.** Authorising at handshake is both cheaper and safer than per message.

**Alternatives.** Per-message authorisation; polling; server-sent events.

**Tradeoffs.** Handshake-time authorisation means a revoked membership does not immediately
close an open socket. For group chat that is acceptable; for something security-critical it
would not be.

**Follow-ups.** "What if membership is revoked mid-session?" · "How do messages cross
processes?" · "Why not SSE?"

**What interviewers expect.** The Redis channel-layer point, and its latent bug: `group_send`
crosses processes via Redis, and the in-memory fallback works *only* because there is
currently exactly one process. Add a second and messages published by one become invisible to
sockets held by the other — silently. It is a bug that is currently masked by a deployment
constraint, which is a good example of a latent failure waiting on a configuration change.

---

### Q42. If you could change one concurrency decision, what would it be?

**Ideal answer.** I would have measured the threading model before building on it, rather than
after an outage forced me to. Everything downstream — the rate limits, the pooling work, the
capacity ceiling — is shaped by a property of the framework I did not know for most of the
project. It cost me a production outage and two review rounds of wrong attribution. The
specific change: a twenty-line experiment driving a sleeping view at increasing concurrency
and recording thread identity, run in week one. That is an hour of work that would have
reframed months of decisions.

**Why we chose this.** Reflection.

**Alternatives.** Choosing FastAPI; running WSGI plus a separate socket process; not
supporting WebSockets.

**Tradeoffs.** Knowing earlier would probably have pushed me toward the WSGI-plus-ASGI split,
which is more moving parts but preserves REST parallelism. On a 512 MB instance that is still
hard, so the outcome might not have changed — but the *reasoning* would have been explicit
rather than accidental.

**Follow-ups.** "Would you use Django again?" · "What would you measure first next time?" ·
"How do you avoid this class of mistake?"

**What interviewers expect.** A generalisable principle, not just regret. Mine: **measure the
properties of your runtime before you build load-bearing assumptions on top of it** — and
specifically, verify that your test harness can reproduce the failure class you care about.
Both of my worst methodological errors were harness problems: `AsyncClient` hiding
per-thread behaviour, and a control taken while the service was still saturated.

---

## Part 1 Recap — The Five Stories to Have Ready

If you remember nothing else, have these five ready to deploy on demand. Each one has a
measurement, a surprise, and a decision.

| # | Story | The one-line hook |
|---|---|---|
| 1 | **The 40-concurrent outage** | Right mechanism, wrong cause — Argon2 really does allocate 19.1 MiB per hash, and it was irrelevant because requests never overlap. |
| 2 | **`CONN_MAX_AGE` never worked** | Configured, commented, reviewed, and completely inert — 20 requests opened 21 sockets. |
| 3 | **The silent cache** | Accepted writes, returned nothing; disabled all rate limiting with zero errors and a green test suite. |
| 4 | **Dead password validators** | Configuration that is never invoked looks identical to configuration that works. |
| 5 | **The `AsyncClient` refutation** | I disproved the correct hypothesis with a harness that structurally could not reproduce the bug. |

Notice the common thread, and say it out loud if you get the chance: **four of the five are
silent failures.** Nothing crashed, nothing logged, and in three cases the tests passed. The
engineering response — boot probes, mutation testing, controls, honest labelling of hypothesis
versus proof — is what the whole project is really about.

---

*End of Part 1 (Questions 1–42). Part 2 — Database Design, Caching & Redis — on approval.*
