# SparkLM — Configuration & Feature Flag Register

**Owner:** Kurapati Sai Suhas · **Created:** M4 Phase C · **Enforced by:**
`backend/LearnLM/common/test_feature_flags.py`

Every environment variable the backend reads is listed here. The register is not
documentation-by-convention: a test scans the codebase for `os.getenv` /
`os.environ.get` and fails if a variable is undocumented, or if this file lists one that
no longer exists. Adding config without an entry breaks the build.

**Why this exists.** M4 review counted seven flags with no owner, no expiry and no
lifecycle. `CURRICULUM_GATE_ENFORCE` had been off "until after the interview" since
July. Flags without expiry dates become permanent by accident, and dead configuration is
indistinguishable from working configuration — a failure mode this codebase has hit three
times (dead password validators, an inert throttle, a non-persisting cache).

**Types.** `flag` = changes behaviour, expected to be resolved and removed ·
`tuning` = an operational value, permanent but reviewable ·
`secret` = credential, permanent · `env` = deployment identity, permanent.

---

## Feature flags — these have expiry dates

| Variable | Prod | Purpose | Added | Expiry | Decision |
|---|---|---|---|---|---|
| `CURRICULUM_GATE_ENFORCE` | `false` | Server-side enforcement of DAG prerequisites on `/api/code/next/`. When off, the gate is computed and returned but not enforced. | M7 (2026-07) | **2026-11-01** | **Stays off.** See below. |

### `CURRICULUM_GATE_ENFORCE` — resolved, stays off

The M4 plan required this to be "resolved" in Phase C. Resolved does not mean flipped.

Enforcing it would block a student from starting a topic whose prerequisites are
unmastered, where mastery is `accuracy ≥ 0.8 over ≥ 3 reviews`. Three reviews is a very
small sample — two correct out of three is 67% and fails the gate on one data point. With
**40 submissions across the whole platform**, most topics have no mastery signal at all,
so enforcement would lock nearly everything for nearly everyone.

The frontend already discourages locked topics, so the server-side gate is defence in
depth rather than the primary mechanism, and off costs little.

**Trigger to revisit:** median reviews per practiced topic ≥ 10, or a Bayesian
confidence threshold replaces the point estimate (roadmap M11, belief layer). Whichever
lands first. Re-dated to 2026-11-01 with that condition attached, rather than left
open-ended.

### ENABLE_SHAP_XAI — RETIRED (M1/P1.1, 2026-08-09)

Removed, along with the code it gated. The earlier entry argued it was a permanent
deployment switch rather than a staged rollout, because the web tier is deliberately
torch-free and `XAIEngine` could not import on the production instance regardless of the
value. That reasoning was right, and P1.1 followed it to its conclusion: a switch that can
only ever hold one value is not a flag, it is dead configuration.

The SHAP-over-GCN implementation, the GCN engine, the ONNX export path and the synthetic
data generator were deleted in the same phase. `NextProblemView._compute_xai` now has a
single heuristic path. **The response schema did not change** — `dominant_factor`,
`success_probability`, `shap_values`, `weak_topics` and `recommendation` are all still
present and are pinned by `test_xai_payload_matches_frontend_schema`.

Re-enabling real attribution is no longer a flag flip; it is a worker-tier project, and it
belongs to M6's decision about whether the routing engine earns further ML investment.

---

## Tuning values — permanent, reviewable

| Variable | Prod | Purpose | Review trigger |
|---|---|---|---|
| `ADMISSION_LIMIT` | `12` | Max in-flight requests before shedding with 503 + `Retry-After`. `0` disables. Derived from measured serialised throughput, not chosen. | Any change to worker count or instance size. **Per-process** — N workers admit N×12. |
| `DRF_NUM_PROXIES` | `1` | DRF's proxy depth. **Bypassed** by every current throttle class (`common/throttling.py` keys on the first XFF hop because Render's last hop rotates). Retained because it is correct again behind a stable proxy. | Moving off Render, or in front of a CDN. |
| `SENTRY_TRACES_RATE` | unset | Sentry performance sample rate. | Sentry quota pressure. |
| `JUDGE0_URL` | unset | Overrides the Judge0 base URL. Defaults from `JUDGE0_API_HOST`. | Self-hosting Judge0 (roadmap M15+). |
| `JUDGE0_CPU_TIME_LIMIT` | unset | Seconds sent as `cpu_time_limit` on every submission. **Unset means the field is omitted**, which is what production has always done. Judge0 enforces `max_cpu_time_limit` server-side and REJECTS anything above it — a rejected submission becomes `GradingUnavailable`, i.e. a 503 for every learner. Set only after confirming the deployed instance's ceiling. | Pinning a reproducible TLE boundary once the Judge0 deployment's limits are known (M2 P2.6). |
| `JUDGE0_MEMORY_LIMIT` | unset | KB sent as `memory_limit`. Same omit-by-default rule and the same rejection risk as above. | As above. |
| `JUDGE0_PYTHON_LANGUAGE_ID` | `92` (Python 3.11.2) | Judge0 language id used for **every** Python execution — learner submissions, oracle runs, the quality gate and hidden-test reconciliation all resolve it from `common.languages`. Was `71` (Python 3.8.1), which cannot parse PEP 585 subscripted generics (`list[list[str]]` raises `TypeError: 'type' object is not subscriptable` at class-definition time); 772 of 2,926 Python starters use that syntax and could not execute at all. 3.11 is the oldest available option that supports PEP 585, chosen to minimise drift from 3.8. A non-integer value raises at import rather than falling back. | Rolling the runtime back (set `71`) or forward (`100` = 3.12.5, `109` = 3.13.2, `113` = 3.14.0) — all on the same Judge0 deployment and key, so this is a selection, not an upgrade. Verify equivalence on already-verified questions before changing it: q3309 reproduces all 12 stored outputs identically under both 71 and 92. |
| `RESEED_GEMINI_MODEL` | `gemini-2.5-flash` | Model id used by the reseed content generator's Gemini provider (`groups/reseed_generation.py`). A model id is configuration, not code: `ai_services.py` hard-codes `llama-3.3-70b-versatile` at two call sites and that model has since been withdrawn, so every Groq path in the app now 404s and fixing it needs a code change. `probe_provider()` reports whether the configured id is actually offered by the key, by listing models rather than generating. | A provider retiring the model, or moving to a cheaper/stronger one. Free tier is 20 requests/day — exhausted by four questions at three attempts. |
| `RESEED_GROQ_MODEL` | `openai/gpt-oss-120b` | Model id used by the reseed content generator's Groq provider. Default chosen because it is what the current key actually offers — verified by listing, not assumed. | As above. The generator fails fast on 404/429 rather than retrying, so a withdrawn model is reported once per question instead of three times. |

---

## Deployment identity — permanent

| Variable | Prod | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `false` | Debug mode. Also gates `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER`, **and whether `/media/` is routed at all** (see §Known gap). |
| `DJANGO_ALLOWED_HOSTS` | `sparklm-api.onrender.com` | Host header allowlist. |
| `CORS_ALLOWED_ORIGINS` | Vercel origin | Browser origins permitted to read API responses. |
| `CSRF_TRUSTED_ORIGINS` | API origin | Trusted origins for the admin's session-cookie CSRF. |
| `SENTRY_ENV` | unset | Environment label in Sentry. |

---

## Secrets — permanent, never in git

`render.yaml` marks each `sync: false`.

| Variable | Purpose | Rotation impact |
|---|---|---|
| `SECRET_KEY` | Django signing. Falls back to a dev value that the boot guard refuses in production. | Invalidates sessions **and** password-reset links. Also JWTs unless `JWT_SIGNING_KEY` is set. |
| `JWT_SIGNING_KEY` | JWT signing, split from `SECRET_KEY` (M4 Phase A). Unset ⇒ falls back to `SECRET_KEY`. | ⚠️ Setting or changing it is a **fleet-wide logout**. |
| `POSTGRES_{DB,USER,PASSWORD,HOST,PORT}` | Neon connection. | Restart required. |
| `REDIS_URL` | Upstash. Cache, throttle store **and Channels layer**. | ⚠️ Unset ⇒ in-memory fallback: throttles become per-process and `group_send` stops crossing processes, **silently**. |
| `GROQ_API_KEY` | Primary LLM. | Content generation fails; NIM fallback is quota-only. |
| `NIM_API_KEY` | NVIDIA NIM fallback, daily-quota errors only. | Fallback unavailable. |
| `GEMINI_API_KEY` | `text-embedding-004` for visual search. | Embedding search degrades. |
| `JUDGE0_API_{KEY,HOST}` | RapidAPI Judge0. Host **must** match the product the key is registered for. | Grading returns 503. |
| `GOOGLE_CLIENT_ID` | Google Sign-In audience check. | `/api/auth/google/` returns 503 by design. |
| `N8N_WEBHOOK_URL` | Agentic coach. | Falls back to static hints. |
| `SENTRY_DSN` | Error tracking. | No-ops when unset. |
| `EMAIL_HOST_{USER,PASSWORD}` | SMTP for the settings test-email endpoint. | Test email fails. |
| `AWS_STORAGE_BUCKET_NAME` | **The switch for durable object storage (M5 Phase 3).** Unset ⇒ `FileSystemStorage`, i.e. uploads do not survive a deploy. Set ⇒ private S3-compatible bucket + signed URLs. | Uploads revert to the ephemeral disk; existing objects in the bucket become unreachable, though nothing is deleted. |
| `AWS_S3_ENDPOINT_URL` | S3-compatible endpoint. This is the only vendor-specific value — Cloudflare R2, Backblaze B2, AWS S3 and MinIO all work. Unset ⇒ real AWS S3. | Wrong endpoint ⇒ every upload and signature fails. |
| `AWS_S3_REGION_NAME` | Region. `auto` for R2. | Signature mismatch on providers that validate region. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Bucket credentials. **Scope them to the one bucket** — they can read every object in it. | Uploads and signing fail; the app degrades to results without links rather than erroring. |
| `AUTH_V2_COOKIES` | **The switch for Authentication v2 (M5 Phase 4).** Off ⇒ refresh tokens in the response body and `localStorage`, no rotation, no cookie — today's behaviour byte-for-byte. On ⇒ httpOnly refresh cookie, rotation with replay detection, refresh token absent from every response body. | Turning it on requires the new frontend to be deployed FIRST. Turning it off is a safe rollback: `read_refresh_token()` accepts the cookie or the body, so nobody is logged out in either direction. |
| `AUTH_COOKIE_SECURE` | `Secure` attribute on the refresh cookie. Defaults to `not DEBUG`. | Setting it false in production would let the refresh cookie travel over plain HTTP. Only ever set it false for local HTTP development. |
| `AUTH_COOKIE_SAMESITE` | Defaults to `None`, which is **required**, not a weakening: the SPA (Vercel) and API (Render) are different sites, so `Lax` would never send the cookie at all. | `Lax`/`Strict` silently break refresh in the deployed topology. `None` is why the refresh endpoint requires the `X-SparkLM-Client` header. |
| `AUTH_COOKIE_PATH` | Cookie scope, default `/api/`. | Narrower breaks refresh or logout; `/` widens exposure for no gain. |
| `SIGNED_URL_TTL` | Seconds a download URL stays valid. Default `300`. | Longer ⇒ a leaked URL is useful for longer; a URL that outlives the session recreates the unauthenticated `/media/` problem. Do not raise without a reason. |

---

## Test-database isolation (M2 P2.7e)

`pytest` must never reach production. `pytest.ini` loads
`-p sparklm_test_isolation`, which rewrites `POSTGRES_*` to a local database
**before** pytest-django configures Django; the rootdir `conftest.py` then
asserts the redirect held and aborts the session if it did not.

This exists because `.env` carries production Neon credentials for the P2.7e
census. Without the redirect, pytest derives its test database from those
credentials and attempts `CREATE DATABASE test_neondb` **on production** — which
failed only because the census role is read-only, and would have succeeded with
a write-capable role.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `TEST_POSTGRES_HOST` | Test database host. Default `127.0.0.1`. | Must be loopback. A remote value is refused by `conftest.pytest_configure`, which exits the session rather than running. |
| `TEST_POSTGRES_DB` | Test database name. Default `learnlm_db` (pytest prefixes `test_`). | A mismatch with the conftest expectation aborts the run. |
| `TEST_POSTGRES_USER` | Test role. Default `postgres`, per `docker-compose.yml`. | Needs CREATE DATABASE locally; a read-only role cannot run the suite. |
| `TEST_POSTGRES_PASSWORD` | Password for the local test role. Default is the committed docker-compose value — not a secret. | Never put a production credential here. |
| `TEST_POSTGRES_PORT` | Default `5432`. | — |

**These are test-only.** They are read by `sparklm_test_isolation.py` and
`conftest.py`, never by application code, and they are not set in Render.

## Known gap — media storage is ephemeral

Not a flag, recorded here because it is configuration-shaped and it **blocks approved
Phase C work**.

`MEDIA_ROOT` is a local directory inside the container, `render.yaml` declares **no
persistent disk**, and `LearnLM/urls.py` routes `/media/` only when `DEBUG` is true. So on
production:

- uploaded files do not survive a deploy or restart;
- they are not retrievable by URL even while they exist.

**Mitigated for RAG in M4 Phase C.** `StudyMaterial.extracted_text` holds the text
extracted once at upload, while the file is guaranteed to exist. RAG reads that column
instead of the file, so it keeps working after the file is swept away. Measured: extraction
is 398 ms of a 398.5 ms preparation step, so this removes essentially all of it.

**Still unfixed for everything else** — file download, preview, and the vision path all
read the file directly and remain broken across deploys, as does re-extraction for the 4
materials uploaded before Phase C (their files are already gone). The general fix is
object storage (`STORAGES` default backend → S3-compatible), which is new infrastructure
and therefore Milestone 5 scope.

---

## Pre-image capture connection (M2 P2.7, blocker J8)

A SEPARATE database alias, `preimage`, used only by `preimage_capture` /
`preimage_inspect` / `preimage_rollback` via `--alias preimage`. Defined in
`settings.py` only when `PREIMAGE_USER` is set; absent it, the alias does not
exist and the commands fail loudly rather than falling back to `default` — which
is the read-only census role and would fail mid-batch instead.

The role is `learnlm_preimage_rw`, audited to hold read and append rights on the
three pre-image tables — and nothing that can modify or remove a row — plus
read access to `groups_question` so a pre-image can be copied. It holds **no**
write privilege on any grading-truth or learner table, so a capture run through
it cannot alter grading truth even if the code tried.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `PREIMAGE_USER` | Capture role. Its presence is what creates the `preimage` alias. | Unset means the alias is missing and the capture commands error. Setting it to a broadly-privileged role defeats the least-privilege property — the operator gate additionally refuses anything but `learnlm_preimage_rw` on production. |
| `PREIMAGE_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `PREIMAGE_DB` | Database name. Default `neondb`. | A different database means pre-images land where the app does not grade from. |
| `PREIMAGE_HOST` | Neon endpoint for the capture connection. | A different endpoint or branch captures a copy of the data rather than the data. |
| `PREIMAGE_PORT` | Default `5432`. | — |

**Capture only.** These credentials must not be used for remediation writes,
migrations, or anything reading learner state.

## Remediation connection (M2 P2.7)

A THIRD alias, `remediate`, used only by `remediate_statement` via
`--alias remediate`. Defined in `settings.py` only when `REMEDIATE_USER` is set.

The role is `learnlm_remediate_rw`, audited to hold read access to
`groups_question` plus column-scoped write on `content` alone — one column of
eleven. It cannot modify hidden tests, status, trust state, the contract, the
boilerplate or the wrapper, and it cannot add or remove a question. It appends
to the remediation audit table and can only read the batch and pre-image
tables, so it cannot alter the pre-image that would restore its own work.

Separate from `PREIMAGE_*` on purpose: the capture role preserves a question
and must not be able to change it; the remediation role changes one column and
must not be able to append pre-images.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `REMEDIATE_USER` | Remediation role. Its presence is what creates the `remediate` alias. | A broadly-privileged value defeats the least-privilege property. The command additionally refuses any role but `learnlm_remediate_rw` on production, and refuses a role holding more than the operation needs. |
| `REMEDIATE_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `REMEDIATE_DB` | Database name. Default `neondb`. | A different database repairs a copy rather than the data. |
| `REMEDIATE_HOST` | Neon endpoint for the remediation connection. | A different endpoint or branch repairs a fork. |
| `REMEDIATE_PORT` | Default `5432`. | — |

**Statement repair only.** Key repair, contract migration and hidden-test
repair each need their own review and their own column grants.

## Hidden-test remediation connection (M2 P2.7)

A FOURTH alias, `hiddentest`, used only by `remediate_hidden_tests` via
`--alias hiddentest`. Defined in `settings.py` only when `HIDDENTEST_USER` is
set.

The role is `learnlm_hidden_test_rw`, audited to hold read access to
`groups_question` plus column-scoped write on `hidden_test_cases` alone. It is
the **mirror image** of `learnlm_remediate_rw`: that role can change a statement
and not a key, this one a key and not a statement. Neither can change status,
trust state, the contract, the boilerplate or the wrapper, and neither can add
or remove a question.

That mirroring is the point. The remediation design fixed an order — statement
first, keys second — and two column-scoped roles make it a privilege boundary
rather than an instruction an operator has to remember.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `HIDDENTEST_USER` | Hidden-test repair role. Its presence is what creates the `hiddentest` alias. | A broadly-privileged value defeats the least-privilege property. The command additionally refuses any role but `learnlm_hidden_test_rw` on production, and refuses a role that also holds `content` UPDATE — an over-granted role is rejected, not used. |
| `HIDDENTEST_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `HIDDENTEST_DB` | Database name. Default `neondb`. | A different database repairs a copy rather than the data. |
| `HIDDENTEST_HOST` | Neon endpoint for the hidden-test connection. | A different endpoint or branch repairs a fork. |
| `HIDDENTEST_PORT` | Default `5432`. | — |

**Hidden-test repair only**, and only of stored *form*: the command cannot
change, add or remove a case `stdin`.

## Contract remediation connection (M2 P2.7)

A FIFTH alias, `contract`, used only by `remediate_contract` via
`--alias contract`. Defined in `settings.py` only when `CONTRACT_USER` is set.

The role is `learnlm_contract_rw`, holding read access to `groups_question`
plus column-scoped write on `execution_contract_version` alone.

A contract migration is a third kind of authority over the same row: it changes
neither what is asked nor what answer is recorded, but **how the stored inputs
are delivered to the learner's code**. Sharing a role with statement or
hidden-test repair would mean whoever can edit a text can also re-point the
question at a different harness.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `CONTRACT_USER` | Contract-repair role. Its presence is what creates the `contract` alias. | A broadly-privileged value defeats the least-privilege property. The command additionally refuses any role but `learnlm_contract_rw` on production, and refuses a role that also holds `content`, `hidden_test_cases`, `boilerplate_code` or `hidden_wrapper_code` UPDATE. |
| `CONTRACT_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `CONTRACT_DB` | Database name. Default `neondb`. | A different database migrates a copy rather than the data. |
| `CONTRACT_HOST` | Neon endpoint for the contract connection. | A different endpoint or branch migrates a fork. |
| `CONTRACT_PORT` | Default `5432`. | — |

**Migration to v3 only.** The command refuses any other target, and refuses a
question whose stored cases do not all bind cleanly under v3 — including one
that binds only by guessing an undeclared parameter type.

## Boilerplate remediation connection (M2 P2.7)

A SIXTH alias, `boilerplate`, used only by `remediate_boilerplate` via
`--alias boilerplate`. Defined in `settings.py` only when `BOILERPLATE_USER` is
set.

The role is `learnlm_boilerplate_rw`, holding read access to `groups_question`
plus column-scoped write on `boilerplate_code` alone.

This is the column with the widest reach per byte: the starter is the code every
learner is **handed**, and it is also the declaration the execution adapter
binds arguments from. A role able to edit it decides both what a learner starts
from and how their submission is called, which is why it is separate from the
roles that edit the text, the keys and the contract version.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `BOILERPLATE_USER` | Boilerplate-repair role. Its presence is what creates the `boilerplate` alias. | A broadly-privileged value defeats the least-privilege property. The command additionally refuses any role but `learnlm_boilerplate_rw` on production, and refuses a role that also holds `content`, `hidden_test_cases`, `status`, `trust_state`, `execution_contract_version` or `hidden_wrapper_code` UPDATE. |
| `BOILERPLATE_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `BOILERPLATE_DB` | Database name. Default `neondb`. | A different database repairs a copy rather than the data. |
| `BOILERPLATE_HOST` | Neon endpoint for the boilerplate connection. | A different endpoint or branch repairs a fork. |
| `BOILERPLATE_PORT` | Default `5432`. | — |

**Annotation repair only.** The command compares the before and after starters
as syntax trees and refuses anything but a change to parameter annotations — no
renamed method, no altered body, no new import, no added return annotation, and
no other language's starter touched.

## Oracle / reference connection (M2 P2.7)

A SEVENTH alias, `oracle`, used by `reference_create`, `reference_review` and
`oracle_execute` via `--alias oracle`. Defined in `settings.py` only when
`ORACLE_USER` is set.

The role is `learnlm_oracle_rw`: SELECT on `groups_question`,
SELECT/INSERT/UPDATE on `groups_referencesolution` (the review lifecycle moves
`review_state`, `is_active` and the provenance fields on an existing row), and
SELECT/INSERT on `groups_oracleexecution`.

It is the first operator role whose writes are not `groups_question` at all, and
that is the point: an oracle run reads a question, executes an approved
implementation against it, and records what happened. It cannot rewrite a key, a
statement or a contract, cannot approve a question, and cannot touch the
remediation audit trail or a pre-image.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `ORACLE_USER` | Oracle role. Its presence is what creates the `oracle` alias. | A broadly-privileged value defeats the least-privilege property. The commands additionally refuse any role but `learnlm_oracle_rw` on production, and refuse a role holding UPDATE on `groups_question` or any privilege on `groups_questionapproval`, `groups_remediationaction` or `groups_questionpreimage`. |
| `ORACLE_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `ORACLE_DB` | Database name. Default `neondb`. | A different database records provenance about a copy. |
| `ORACLE_HOST` | Neon endpoint for the oracle connection. | A different endpoint or branch records provenance for a fork. |
| `ORACLE_PORT` | Default `5432`. | — |

**Provenance only.** No command on this connection can write `expected_output`,
`status` or `trust_state` — the writer does not exist, and the grant would
refuse it if it did.

## Question-approval connection (M2 P2.7h-6)

An EIGHTH alias, `approve`, used by `question_approve --alias approve`. Defined
in `settings.py` only when `APPROVE_USER` is set.

The role is `learnlm_approve_rw`, and it is the narrowest of the eight: INSERT
on `groups_questionapproval`, SELECT on the three tables an artifact is
assembled from (`groups_question`, `groups_referencesolution`,
`groups_oracleexecution`), and column-scoped SELECT on `groups_user(id)` — the
FK check Django performs in `full_clean` needs the id and nothing else, so the
role cannot read an email or a password hash.

It holds **no write privilege on `groups_question` at all**. An approver states
a judgement and is structurally unable to enact it; promotion is a separate role
(`learnlm_promote_rw`) on a separate alias. The grant list lives in
`_preimage_ops.APPROVAL_ROLE_GRANTS`, the production DDL is generated from it,
and the test suite proves it both sufficient (a role holding exactly these
completes a real approval over a real second connection) and minimal (dropping
any one line makes the approval fail).

| Variable | What it does | Risk if wrong |
|---|---|---|
| `APPROVE_USER` | Approval role. Its presence is what creates the `approve` alias. | A broadly-privileged value defeats the least-privilege property. `question_approve` additionally refuses any role but `learnlm_approve_rw` on production, and refuses a role holding INSERT/UPDATE/DELETE on `groups_question`, UPDATE/DELETE on `groups_questionapproval`, or any write on `groups_referencesolution` or `groups_oracleexecution`. |
| `APPROVE_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `APPROVE_DB` | Database name. Default `neondb`. | A different database records approval of a copy. |
| `APPROVE_HOST` | Neon endpoint for the approval connection. | A different endpoint or branch approves a fork. |
| `APPROVE_PORT` | Default `5432`. | — |

**One row, never a trust state.** `question_approve` writes a single
`QuestionApproval` and nothing else. `trust_state` has exactly one writer in the
codebase — `question_promote` — and this role cannot perform it.

## Trust-promotion connection (M2 P2.7h-7)

A NINTH alias, `promote`, used by `question_promote --alias promote`. Defined in
`settings.py` only when `PROMOTE_USER` is set.

The role is `learnlm_promote_rw`, and it is the mirror image of the approval
role. It holds `UPDATE (trust_state)` on `groups_question` — **one column** —
and `UPDATE (promoted_at, promoted_by_id)` on `groups_questionapproval`, plus
SELECT on the four tables the artifact is re-derived from. It has **no INSERT on
`groups_questionapproval`**, so a promoter cannot author the judgement it acts
on, and **no UPDATE on `groups_question.status`**, because promotion does not
publish.

The grant list lives in `_preimage_ops.PROMOTION_ROLE_GRANTS`, the production
DDL is generated from it, and the test suite proves it sufficient and minimal
over a real second connection.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `PROMOTE_USER` | Promotion role. Its presence is what creates the `promote` alias. | A broadly-privileged value defeats the least-privilege property. `question_promote` additionally refuses any role but `learnlm_promote_rw` on production, and refuses a role holding INSERT/DELETE on `groups_questionapproval`, UPDATE on any approval column other than the two stamp columns, or UPDATE on any `groups_question` column other than `trust_state` — `status` included. |
| `PROMOTE_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `PROMOTE_DB` | Database name. Default `neondb`. | A different database promotes a copy. |
| `PROMOTE_HOST` | Neon endpoint for the promotion connection. | A different endpoint or branch promotes a fork. |
| `PROMOTE_PORT` | Default `5432`. | — |

**Promotion is not publication.** `is_adaptive_eligible` requires PUBLISHED
**and** ORACLE_VERIFIED. Promoting a question that is not published changes no
learner's experience; it makes the question eligible to be published as trusted.

## Question-status connection (M2 P2.7h-8)

A TENTH alias, `status`, used by `question_status --alias status`. Defined in
`settings.py` only when `STATUS_USER` is set.

The role is `learnlm_status_rw`, the first and only writer of
`Question.status` — a column that had no writer at all until this milestone.
Shaped like the four repair roles: SELECT on the question plus `UPDATE (status)`
and nothing else on it, SELECT on the batch and pre-image tables, SELECT+INSERT
on the action table, and SELECT on the approval chain that the publication edge
re-derives.

It is denied `UPDATE (trust_state)` explicitly, and `learnlm_promote_rw` is
denied `UPDATE (status)`. `is_adaptive_eligible` is PUBLISHED **and**
ORACLE_VERIFIED, so a role holding both columns could turn a question on by
itself; no role holds both. It is also denied UPDATE/DELETE on
`groups_remediationaction` — a role that can rewrite the record of what it did
is not audited — and any write on the pre-image tables that make its own change
reversible.

| Variable | What it does | Risk if wrong |
|---|---|---|
| `STATUS_USER` | Status role. Its presence is what creates the `status` alias. | A broadly-privileged value defeats the least-privilege property. `question_status` additionally refuses any role but `learnlm_status_rw` on production, and refuses one holding UPDATE on any other `groups_question` column (`trust_state` first among them), any write on the approval, reference or execution tables, any UPDATE/DELETE on the audit trail, or any write on the pre-image. |
| `STATUS_PASSWORD` | Password for that role. | Never commit it; `.env` is gitignored. |
| `STATUS_DB` | Database name. Default `neondb`. | A different database publishes a copy. |
| `STATUS_HOST` | Neon endpoint for the status connection. | A different endpoint or branch publishes a fork. |
| `STATUS_PORT` | Default `5432`. | — |

**The legal graph** is `Question.STATUS_TRANSITIONS`: `DRAFT → PENDING_REVIEW`,
`PENDING_REVIEW → PUBLISHED`, and `PUBLISHED → PENDING_REVIEW` for withdrawal.
`Question.STATUS_BLOCKED` is read by the census and the oracle pipeline and has
no writer.

## Adding configuration

1. Add the entry here **in the same commit** as the code that reads it.
2. Give a `flag` an expiry date and the condition that resolves it. Not "when we get to it".
3. At expiry: remove the flag, or promote its default, or re-date it **with a reason**.
   Silent extension is how `CURRICULUM_GATE_ENFORCE` reached four months.
