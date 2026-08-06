# Frontend — how the SPA reaches the API

**Owner:** Kurapati Sai Suhas · **Created:** M6 / N3 Phase 1a · **Enforced by:**
`studysphere-ai-11/src/services/networkAccess.test.ts`

## The rule

**API access should go through `src/services/api.js`.** No component may call
`fetch` or import `axios` directly, and no request may use a relative
`/api/...` URL.

**This is not yet universally true, and the guard says so.** Six files still
reach the API directly; they are listed in `PHASE_1B_ALLOWLIST` and are Phase
1b's remaining work. The guard enforces the rule everywhere *except* that
list, and the list can only shrink.

An earlier version of this document claimed *"All API access goes through
src/services/api.js"*. That was untrue — `CodingPortal.tsx` makes three calls
through a directly imported axios — and it was not enforced, because the guard
only looked for `fetch(`. Both have been corrected. A rule of record that
overstates coverage is worse than no rule: it invites the next reader to trust
a guarantee that does not hold.

## Why

Three pages — `/schedule`, `/notifications`, `/settings` — were broken in
production for months and nothing reported it.

Each hand-rolled a `fetch` to a **relative** path such as `/api/schedule/`. On
Vercel that resolves against the SPA's own origin, where the catch-all rewrite in
`vercel.json` serves `index.html` with **HTTP 200**. The pages parsed the app's
own HTML as JSON, threw, and swallowed the error in a bare `.catch`. Measured
against production:

```
GET https://spark-lm-3y3e.vercel.app/api/notifications/
  -> HTTP 200, Content-Type: text/html      <-- the SPA shell
GET https://sparklm-api.onrender.com/api/notifications/
  -> HTTP 401                               <-- the real API
```

No unit test could see it: the failure is a property of the deployed origin, not
of any function. The pages also inherited three further defects from bypassing
the shared client — no `baseURL`, no 401 refresh-and-retry, and a hand-built
`Authorization` header reading a `localStorage` key **nothing ever wrote**, so
they sent `Bearer null`.

## What the shared client provides

| | |
|---|---|
| `baseURL` | `${VITE_API_URL}/api` — the fix for the relative-path bug |
| `Authorization` | Bearer token from memory, one place |
| `X-SparkLM-Client` | CSRF sentinel required by refresh/logout |
| `withCredentials` | sends the httpOnly refresh cookie |
| 401 interceptor | single-flight refresh, then one retry |

Client paths omit the `/api` prefix — `baseURL` already ends in it. Writing
`/api/notifications/` yields `/api/api/notifications/` and a 404.

## Migration status

| Phase | Files | Transport | Status |
|---|---|---|---|
| **1a** | `Schedule.tsx`, `Notifications.tsx`, `Settings.tsx` | — | ✅ migrated |
| **1b** | `ReviewQueueCard`, `LearningPathVisualizer`, `CodingOnboardingModal`, `AdaptiveCodingPortal`, `CodingHub` | raw `fetch` | ⬜ pending |
| **1b** | `CodingPortal.tsx` | **raw `axios`** | ⬜ pending |

The **six** pending files are listed in `PHASE_1B_ALLOWLIST` in the guard.
**Delete an entry when you migrate its file** — the guard fails on a stale
entry, so the list can only shrink.

`CodingPortal.tsx` was added by the Phase 1a audit. It had been invisible: the
guard checked `fetch(` only, and this file imports axios directly. It is a
routed page (`/code`) making three API calls outside the shared instance, so
Phase 1b would have missed it. It carries neither Phase 1a defect — it uses
`VITE_API_URL` and `getAccessToken()` — but it bypasses the 401 refresh
interceptor and hand-builds its own `Authorization` header.

## What the guard checks

1. No `fetch` call targets a relative `/api` path, **repository-wide**. Already
   true everywhere: the pending calls all use `VITE_API_URL`.
2. Raw `fetch(` appears only in allowlisted files.
3. **A direct `axios` import appears only in allowlisted files.** Matched on the
   module specifier `"axios"`, never on the local identifier — which is what
   makes renaming useless as an evasion. `import a from "axios"`,
   `import * as http from "axios"`, `require("axios")` and `import("axios")`
   are all the same string at the end. Multi-line imports need no special
   handling: the patterns use `\s*`, which spans newlines.
4. **Nothing outside the client calls `axios.create(`.** A second instance has
   none of the shared interceptors — no refresh, no CSRF sentinel, no
   `baseURL` — while looking like centralised code.
5. The allowlist has no stale entries, and no migrated page is in it. An entry
   earns its place by still using **either** transport.
6. Migrated pages import the client, use neither transport, and build no
   `Authorization` header.

`import type ... from "axios"` is deliberately allowed: it is erased at build
time and cannot issue a request. Blocking it would be theatre and would push
people toward `any`.

### The detectors are themselves tested

Checks 1–4 all assert `expect(offenders).toEqual([])`. **That shape cannot fail
for the reason it claims.** A working detector that finds nothing and a broken
detector that finds nothing return the same empty list. Confirmed by mutation:
disabling the relative-`/api` check, the raw-`fetch` check, or the
`axios.create` check individually left every test green.

So the file also runs the detectors against inline fixtures — eleven evasions
that must be caught, six legitimate patterns that must not be. Those fixtures
are what make the mutants die. Add one whenever you touch a detector; they run
in-process, so unlike file injection they are deterministic.

> Injecting a violation into a real file and re-running is a useful manual
> check, but it is **unreliable in this working tree** — the repository is on a
> OneDrive-synced path, and rapid write→test→restore cycles race the sync
> client. It produced two phantom failures during this work. Trust the
> in-process fixtures.

## Known limits

The two transports are **not** guarded to the same strength, and the difference
is worth understanding before trusting either.

- **axios: aliasing does not help.** The check matches the module specifier, so
  renaming the binding changes nothing. Verified against eleven evasions —
  default import, single quotes, renamed binding, namespace import, mixed
  import, multi-line import, `require`, dynamic `import()`, `axios.create`,
  `axios.create` split across lines, and a relative-`/api` fetch.
- **fetch: aliasing IS still undetected.** `const f = fetch; f("/api/x")` and
  `(globalThis.fetch)("/api/x")` both evade it — verified. `fetch` is a global
  with no import to match, so there is no equivalent anchor. This is the one
  real asymmetry: the fetch rule catches accidents and regressions, not
  deliberate circumvention.
- **Neither is a substitute for AST analysis.** Both checks are textual. An
  ESLint rule (`no-restricted-imports` + `no-restricted-globals`) would close
  the fetch gap properly and is the right future addition; it was not worth
  blocking Phase 1a on.
- **Trailing comments are scanned.** Whole-line comments are skipped so that
  documenting the rule does not break the build — verified for both `//` and
  `/* */` — but `doStuff(); // fetch("/api/x")` would still trip it. Stripping
  to end-of-line was rejected because it would also cut through
  `"http://localhost:8000"` inside a string literal and could hide a real
  violation. A false negative in a guard is worse than a false positive.
- **Non-API `fetch` is also blocked** outside the allowlist. Intentional: there
  is no current use case, and an exception should be a deliberate edit here.

## WebSockets are exempt, deliberately

`GroupChat`, `useGroupChat` and `LiveCollaborativeWorkspace` call
`getAccessToken()` directly, and `ProtectedRoute` uses it to decide routing.
axios cannot open a WebSocket — a rule forbidding this would be wrong, not
merely inconvenient. The guard does not assert anything about `getAccessToken`.
