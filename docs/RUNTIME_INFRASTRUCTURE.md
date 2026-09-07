# Runtime infrastructure (Phase 1 M10)

## Judge0 diagnosis — NOT_SUBSCRIBED

Four milestones reported "Judge0 is down (429/403)" and inferred **quota
exhaustion** from the status code. That inference was wrong. Asked directly:

| request | status | body |
|---|---|---|
| `GET /languages` (judge0-ce) | **403** | `{"message":"You are not subscribed to this API."}` |
| `GET /languages` (judge0-**extra**-ce) | **403** | `{"message":"You are not subscribed to this API."}` |
| `POST /submissions` | 429 | `{"message":"Too many requests"}` |

**Classification: `NOT_SUBSCRIBED` — account/subscription level, stated by the
provider.** Ruled out by evidence:

- **not credentials** — a bad key returns `401 Invalid API key`; this key is
  accepted and routed (RapidAPI-branded errors, `x-rapidapi-region` header)
- **not quota** — quota is worded "exceeded the monthly limit"; a read that
  executes nothing is also refused, which a submission quota would not do
- **not the wrong host** — both Judge0 products answer identically
- **not a rate limit** — the 429 on POST is a *consequence* of unsubscribed
  access being throttled, not an independent cause

**Remedy (operator-only):** subscribe to Judge0 CE on RapidAPI, or point
`JUDGE0_URL`/`JUDGE0_API_KEY` at a self-hosted Judge0.

No secrets were printed at any point — the key was reported as length plus a
digest prefix only.

## Observability

`groups/judge0_diagnostics.py` classifies on the **provider's words first**,
status code second, because a 403 alone cannot separate a subscription problem
from a quota problem and the two have opposite remedies.

| category | remedy | retryable |
|---|---|---|
| `authentication` | fix credentials | no |
| `not_subscribed` | subscribe, or self-host | **no** |
| `quota` | wait for reset or upgrade | yes |
| `rate_limited` | back off | yes |
| `malformed_request` | a defect *here* | no |
| `provider_unavailable` | retry later | yes |
| `execution_failure` | the learner's problem | — |
| `unknown` | unclassified; raw detail carried | — |

`unknown` is deliberate: a classifier that forces every response into a known
bucket is how "not subscribed" spent four milestones being called "quota".

The runner adds `judge0_category`, `judge0_detail` and `judge0_retryable`
**beside** the existing `error` key — whose meaning is unchanged, because
`GradingService` and `OracleService` both raise on its presence. **No retry
loop was added**, and a test asserts `_run_on_judge0` contains no loop:
retrying an unsubscribed call cannot succeed and burns an allowance the
account does not have.

## Local runtimes

Installed during M10, with the operator's explicit approval:

| tool | version | source |
|---|---|---|
| `javac` / `java` | 21.0.12.1 (Microsoft OpenJDK, Hotspot) | `winget Microsoft.OpenJDK.21` |
| `gcc` | 16.1.0 (MinGW-W64 ucrt-posix-seh) | `winget BrechtSanders.WinLibs.POSIX.UCRT` |
| `g++` | 16.1.0 (MinGW-W64 ucrt-posix-seh) | same package |

Already present: `python` 3.12.10, `node` v24.15.0.

**PATH note:** winget writes the *user* PATH, so a shell started before the
install will not see them. Tests skip with a named reason rather than failing —
a suite that fails on shell provenance teaches people to ignore it.

## Runtime strategy — (C) both, with a hard boundary

```
development / contract validation   local runtimes
production / oracle                 Judge0
```

**A working local compiler is not a reason to replace Judge0 in production.**
Judge0 is a sandbox with resource limits and status classification; `g++` on a
laptop is neither. The local runner used by the pilots is **test-scoped by
construction** — it lives in the test module, nothing importable by production
references it, and it validates the *contract*, not the sandbox.

## Pilot results

All executed through `GradingService._build_executable` and `prepare_stdin` —
the real seam — then compiled and run.

### C++ (Judge0 language id 54)

| case | source passthrough | outcome | output |
|---|---|---|---|
| scalar (`x*2`) | ✅ verbatim | OK | `42` |
| array i/o (sum) | ✅ verbatim | OK | `6` |
| algorithm (sort) | ✅ verbatim | OK | `1 1 3 4 5` |
| null-deref | — | **RUNTIME_ERROR** | — |
| infinite loop | — | **TIMEOUT** | — |
| syntax error | — | **COMPILE_ERROR** | — |

### C (Judge0 language id 50)

| case | source passthrough | outcome | output |
|---|---|---|---|
| scalar (`x*2`) | ✅ verbatim | OK | `42` |
| array i/o (sum) | ✅ verbatim | OK | `6` |
| algorithm (max) | ✅ verbatim | OK | `5` |

The self-contained contract holds: **the learner's source reaches the compiler
byte-identical**, stdin untransformed, no wrapper, no injected `main()`.

### Java (Judge0 language id 62) — real v2 reflection harness

| case | outcome | output |
|---|---|---|
| scalar | OK | `42` |
| array | OK | `6` |
| multiple arguments | OK | `12` |
| two public methods | **refused, exit 2**, "exactly one public method" | — |

**This is not broad Java readiness.** Three passing examples validate the
harness; 1,153 Java starters remain NOT_READY and the **Java structural
adapter is still not wired in** — a JVM makes it testable, not done.

## Runtime matrix

| Language | Local runtime | Judge0 | Scalar | Array | Structural |
|---|---|---|---|---|---|
| Python | ✅ 3.12.10 | BLOCKED | PASS | PASS | PASS |
| Java | ✅ 21.0.12.1 | BLOCKED | PASS | PASS | NOT_APPLICABLE¹ |
| JavaScript | ✅ v24.15.0 | BLOCKED | PASS | PASS | PASS |
| C | ✅ gcc 16.1.0 | BLOCKED | PASS | PASS | NOT_APPLICABLE² |
| C++ | ✅ g++ 16.1.0 | BLOCKED | PASS | PASS | NOT_APPLICABLE² |

¹ The Java structural adapter exists as a written contract but is deliberately
not wired into the v2 Java harness. ² C and C++ are self-contained: the
canonical serialized structure arrives on stdin and the learner parses it —
by design, not by omission.

`BLOCKED` (not `UNKNOWN`): Judge0 has been tested and refused.

## What M10 did NOT change

- **No execution model moved.** Reflection = python/java/javascript;
  self-contained = c/cpp. `V2_WRAPPERS` and `STRUCTURAL_PRELUDES` still have
  no C/C++ entry.
- **No trust artifact was created.** 6 ORACLE_VERIFIED, 6 PUBLISHED, 8
  references, 238 oracle executions, 8 approvals — identical before and after.
- **q7 was not oracle-executed.** Its oracle path is Judge0-only, and Judge0
  is unavailable. q7 remains DRAFT / UNVERIFIED / `verified_language=None`
  with **0 oracle executions**. A compiling reference is not verification.
- **q98 remains quarantined**, answer key untouched. No new evidence was
  produced because no oracle run happened.
- **K2 untouched.** v3 remains Python-only; runtime restoration would not have
  changed that.
