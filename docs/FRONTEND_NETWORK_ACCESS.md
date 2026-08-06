# Frontend — how the SPA reaches the API

**Owner:** Kurapati Sai Suhas · **Created:** M6 / N3 Phase 1a · **Enforced by:**
`studysphere-ai-11/src/services/networkAccess.test.ts`

## The rule

**All API access goes through `src/services/api.js`.** No component calls `fetch`
directly, and no request ever uses a relative `/api/...` URL.

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

| Phase | Files | Status |
|---|---|---|
| **1a** | `Schedule.tsx`, `Notifications.tsx`, `Settings.tsx` | ✅ migrated |
| **1b** | `ReviewQueueCard`, `LearningPathVisualizer`, `CodingOnboardingModal`, `AdaptiveCodingPortal`, `CodingHub` | ⬜ pending |

The five pending files are listed in `PHASE_1B_ALLOWLIST` in the guard. **Delete
an entry when you migrate its file** — the guard fails on a stale entry, so the
list can only shrink.

## What the guard checks

1. No `fetch` call targets a relative `/api` path, **repository-wide**. Already
   true everywhere: the pending calls all use `VITE_API_URL`.
2. Raw `fetch(` appears only in allowlisted files.
3. The allowlist has no stale entries, and no migrated page is in it.
4. Migrated pages import the client and build no `Authorization` header.

## Known limits

- **Aliasing is not detected.** `const f = fetch; f("/api/x")` and
  `(globalThis.fetch)("/api/x")` both evade it — verified. The guard catches
  accidents and regressions, not deliberate circumvention. Catching those needs
  an ESLint rule with AST analysis (`no-restricted-globals`), which is a
  reasonable future addition.
- **Trailing comments are scanned.** Whole-line comments are skipped so that
  documenting the rule does not break the build, but `doStuff(); // fetch("/api/x")`
  would still trip it. Stripping to end-of-line was rejected because it would
  also cut through `"http://localhost:8000"` inside a string literal and could
  hide a real violation.
- **Non-API `fetch` is also blocked** outside the allowlist. Intentional: there
  is no current use case, and an exception should be a deliberate edit here.

## WebSockets are exempt, deliberately

`GroupChat`, `useGroupChat` and `LiveCollaborativeWorkspace` call
`getAccessToken()` directly, and `ProtectedRoute` uses it to decide routing.
axios cannot open a WebSocket — a rule forbidding this would be wrong, not
merely inconvenient. The guard does not assert anything about `getAccessToken`.
