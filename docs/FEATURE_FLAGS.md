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

## Adding configuration

1. Add the entry here **in the same commit** as the code that reads it.
2. Give a `flag` an expiry date and the condition that resolves it. Not "when we get to it".
3. At expiry: remove the flag, or promote its default, or re-date it **with a reason**.
   Silent extension is how `CURRICULUM_GATE_ENFORCE` reached four months.
