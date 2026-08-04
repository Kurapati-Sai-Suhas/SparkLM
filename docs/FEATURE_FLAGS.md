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
| `ENABLE_SHAP_XAI` | `false` | Per-recommendation SHAP attribution via `XAIEngine`. | M5 (2026-07) | **None — permanent** | **Reclassified as `tuning`.** See below. |

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

### `ENABLE_SHAP_XAI` — reclassified, not expiring

This was listed as a flag needing an expiry. On inspection it is not a staged rollout: it
is off because **the web tier is deliberately torch-free**. `requirements.txt` excludes
PyTorch, so `XAIEngine` cannot import on the production instance regardless of this
value, and the 512 MB limit is what makes that permanent rather than temporary. Even with
memory, per-request SHAP is far too slow at 0.1 vCPU.

Giving it an expiry would imply someone will turn it on, which is false while the tier is
torch-free. It is reclassified as a permanent deployment switch and stays. Enabling it
requires the worker tier and an ONNX inference path (`export_onnx.py`), which is Milestone
5 or later.

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
