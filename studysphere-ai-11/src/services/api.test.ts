/**
 * API client contract — token attachment and the 401 refresh flow
 * (M4 Phase B polish).
 *
 * Every authenticated request in the SPA goes through these two axios
 * interceptors, and their failure modes are quiet rather than loud:
 *
 *   - an infinite retry loop if the `_retried` guard breaks;
 *   - a refresh storm if several concurrent 401s each trigger their own
 *     refresh (the classic defect in hand-rolled refresh logic);
 *   - a silent logout if a plain 401 clears storage without redirecting,
 *     which is what the code comment records as the original bug: "it shows
 *     dummy data until I log out and back in".
 *
 * `src/services/api.js` is NOT modified by these tests — this is the auth
 * plumbing and Phase B does not change it. The tests drive it through a
 * stubbed axios adapter so no network is involved.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Requests the stubbed adapter saw, in order. */
let seen: any[] = [];
/** url -> queued responses; each call shifts one off. */
let responses: Record<string, any[]> = {};

function reply(status: number, data: any = {}) {
  return { status, data };
}

/**
 * Loads a fresh copy of the module with a stubbed adapter, so the
 * module-level `refreshPromise` starts null for every test.
 */
async function loadApi() {
  vi.resetModules();
  const axios = (await import("axios")).default;

  const adapter = async (config: any) => {
    // `method` and `data` added for the Task 1 client-contract tests; the
    // existing assertions read only `url`/`headers`, so this is additive.
    seen.push({
      url: config.url,
      method: (config.method ?? "get").toLowerCase(),
      data: config.data,
      headers: { ...config.headers },
    });
    const queue = responses[config.url] ?? [];
    const next = queue.length > 1 ? queue.shift() : queue[0];
    const res = next ?? reply(200, { ok: true });
    const payload = { ...res, config, headers: {}, statusText: "" };
    if (res.status >= 400) {
      const err: any = new Error(`Request failed with status ${res.status}`);
      err.response = payload;
      err.config = config;
      throw err;
    }
    return payload;
  };

  axios.defaults.adapter = adapter as any;
  return await import("./api");
}

beforeEach(() => {
  seen = [];
  responses = {};
  localStorage.clear();
  // api.js navigates on unrecoverable auth failure; jsdom cannot navigate.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname: "/dashboard", href: "" },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("request interceptor", () => {
  it("attaches the in-memory token as a Bearer header", async () => {
    // CHANGED BY THE PHASE 4 FOLLOW-UP. The access token is no longer
    // mirrored into localStorage — it lives only in a module variable, so
    // the test sets it through the exported seam rather than through
    // storage. Seeding localStorage here would now prove nothing.
    const { userAPI, setAccessToken } = await loadApi();
    setAccessToken("tok-123");

    await userAPI.getProfile();

    expect(seen[0].headers.Authorization).toBe("Bearer tok-123");
  });

  it("never writes the access token to localStorage", async () => {
    // The regression guard for the whole follow-up: a mirror reintroduced
    // anywhere puts a script-readable credential back on the page.
    const { setAccessToken } = await loadApi();

    setAccessToken("must-not-persist");

    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("access")).toBeNull();
    expect(localStorage.getItem("access_token")).toBeNull();
  });

  it("sends no Authorization header when there is no token", async () => {
    const { userAPI } = await loadApi();

    await userAPI.getProfile();

    expect(seen[0].headers.Authorization).toBeUndefined();
  });
});

describe("401 refresh flow", () => {
  it("refreshes once and retries the original request", async () => {
    localStorage.setItem("authToken", "expired");
    localStorage.setItem("refreshToken", "refresh-abc");
    responses = {
      "/user/profile/": [reply(401), reply(200, { username: "sam" })],
      "/token/refresh/": [reply(200, { access: "fresh-token" })],
    };
    const { userAPI, getAccessToken } = await loadApi();

    const res = await userAPI.getProfile();

    expect(res.data.username).toBe("sam");
    // The rotated access token is held in memory, never persisted.
    expect(getAccessToken()).toBe("fresh-token");
    expect(localStorage.getItem("authToken")).toBeNull();
    const urls = seen.map((r) => r.url);
    expect(urls).toEqual(["/user/profile/", "/token/refresh/", "/user/profile/"]);
  });

  it("retries with the NEW token, not the expired one", async () => {
    localStorage.setItem("authToken", "expired");
    localStorage.setItem("refreshToken", "refresh-abc");
    responses = {
      "/user/profile/": [reply(401), reply(200, {})],
      "/token/refresh/": [reply(200, { access: "fresh-token" })],
    };
    const { userAPI } = await loadApi();

    await userAPI.getProfile();

    const retry = seen[seen.length - 1];
    expect(retry.headers.Authorization).toBe("Bearer fresh-token");
  });

  it("issues ONE refresh for several concurrent 401s", async () => {
    // The thundering-herd defect: without a shared in-flight promise, N
    // simultaneous 401s produce N refresh calls, and all but one of the
    // resulting tokens is immediately discarded.
    localStorage.setItem("authToken", "expired");
    localStorage.setItem("refreshToken", "refresh-abc");
    responses = {
      "/user/profile/": [reply(401), reply(200, {})],
      "/dashboard/stats/": [reply(401), reply(200, {})],
      // Was userAPI.getStats() -> /user/stats/, a route the backend never
      // served; removed in Task 1. Any third concurrent request proves the
      // same property, so this now uses a real endpoint.
      "/notifications/": [reply(401), reply(200, [])],
      "/token/refresh/": [reply(200, { access: "fresh-token" })],
    };
    const { userAPI } = await loadApi();

    await Promise.all([
      userAPI.getProfile(),
      userAPI.getDashboardStats(),
      userAPI.getNotifications(),
    ]);

    const refreshes = seen.filter((r) => r.url === "/token/refresh/");
    expect(refreshes).toHaveLength(1);
  });

  it("does not retry more than once", async () => {
    // Guards against an infinite loop if the refresh succeeds but the
    // retried request 401s again.
    localStorage.setItem("authToken", "expired");
    localStorage.setItem("refreshToken", "refresh-abc");
    responses = {
      "/user/profile/": [reply(401)],
      "/token/refresh/": [reply(200, { access: "fresh-token" })],
    };
    const { userAPI } = await loadApi();

    await expect(userAPI.getProfile()).rejects.toBeTruthy();

    const attempts = seen.filter((r) => r.url === "/user/profile/");
    expect(attempts).toHaveLength(2);   // original + exactly one retry
  });

  it("still attempts a refresh when no readable token exists, then clears and redirects", async () => {
    // CHANGED BY AUTH V2 (M5 Phase 4). This used to assert that a missing
    // localStorage refresh token meant NO refresh attempt. Under Auth v2
    // the refresh token is an httpOnly cookie the client cannot read, so
    // "nothing in storage" no longer implies "no session" — the client
    // must ask the server and let it decide. The failure behaviour is
    // unchanged: storage cleared, redirect to /auth.
    localStorage.setItem("authToken", "expired");
    responses = {
      "/user/profile/": [reply(401)],
      "/token/refresh/": [reply(401)],
    };
    const { userAPI } = await loadApi();

    await expect(userAPI.getProfile()).rejects.toBeTruthy();

    expect(localStorage.getItem("authToken")).toBeNull();
    expect(window.location.href).toBe("/auth");
    expect(seen.filter((r) => r.url === "/token/refresh/")).toHaveLength(1);
  });

  it("clears storage and redirects when the refresh itself fails", async () => {
    localStorage.setItem("authToken", "expired");
    localStorage.setItem("refreshToken", "stale-refresh");
    responses = {
      "/user/profile/": [reply(401)],
      "/token/refresh/": [reply(401)],
    };
    const { userAPI } = await loadApi();

    await expect(userAPI.getProfile()).rejects.toBeTruthy();

    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(window.location.href).toBe("/auth");
  });

  it("does not attempt refresh for a failed login", async () => {
    // A 401 from /token/ means bad credentials, not an expired session.
    // Refreshing there would be nonsense and could mask the real error.
    responses = { "/token/": [reply(401)] };
    const { authAPI } = await loadApi();

    await expect(authAPI.login("sam", "wrong")).rejects.toBeTruthy();

    expect(seen.filter((r) => r.url === "/token/refresh/")).toHaveLength(0);
  });

  it("passes non-401 errors straight through", async () => {
    localStorage.setItem("authToken", "tok");
    localStorage.setItem("refreshToken", "refresh-abc");
    responses = { "/user/profile/": [reply(500)] };
    const { userAPI } = await loadApi();

    await expect(userAPI.getProfile()).rejects.toBeTruthy();

    expect(seen.filter((r) => r.url === "/token/refresh/")).toHaveLength(0);
  });
});

describe("authAPI token storage", () => {
  it("stores both tokens on successful login", async () => {
    responses = { "/token/": [reply(200, { access: "a-tok", refresh: "r-tok" })] };
    const { authAPI, getAccessToken } = await loadApi();

    await authAPI.login("sam", "correct");

    expect(getAccessToken()).toBe("a-tok");
    expect(localStorage.getItem("authToken")).toBeNull();
    // The refresh token is still stored on the LEGACY path (the backend
    // returned one), which is what keeps a rollback working. Under Auth v2
    // the body carries no refresh token at all.
    expect(localStorage.getItem("refreshToken")).toBe("r-tok");
  });

  it("stores both tokens on Google sign-in", async () => {
    responses = { "/auth/google/": [reply(200, { access: "g-tok", refresh: "gr-tok" })] };
    const { authAPI, getAccessToken } = await loadApi();

    await authAPI.googleLogin("google-credential");

    expect(getAccessToken()).toBe("g-tok");
    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("refreshToken")).toBe("gr-tok");
  });

  it("calls the server and clears both tokens on logout", async () => {
    // CHANGED BY AUTH V2 (M5 Phase 4). Logout is now async and must reach
    // the server: an httpOnly cookie cannot be removed by client script,
    // so clearing localStorage alone would leave a live refresh token in
    // the browser — a logout that logs nobody out.
    localStorage.setItem("authToken", "a");
    localStorage.setItem("refreshToken", "r");
    responses = { "/auth/logout/": [reply(200, { detail: "Signed out." })] };
    const { authAPI } = await loadApi();

    await authAPI.logout();

    expect(seen.filter((r) => r.url === "/auth/logout/")).toHaveLength(1);
    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("refreshToken")).toBeNull();
  });

  it("still clears local state when the logout request fails", async () => {
    // Offline or already signed out. Refusing to clear would strand the
    // user in a half-authenticated UI.
    localStorage.setItem("authToken", "a");
    localStorage.setItem("refreshToken", "r");
    responses = { "/auth/logout/": [reply(500)] };
    const { authAPI } = await loadApi();

    await authAPI.logout();

    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("refreshToken")).toBeNull();
  });
});

// ── N3 Phase 1a, Task 1: the client's contract with the backend ─────────
//
// Three pages (/notifications, /schedule, /settings) were broken in
// production because they hand-rolled fetches instead of using this client.
// Migrating them (Tasks 2-4) is only safe if the client itself is correct —
// and it was not: eight methods addressed routes the backend has never
// served, and every one of them had zero call sites, so nothing ever failed
// loudly enough to notice.
//
// The path-contract test below is the guard that would have caught the
// whole class. It compares every path this client requests against the
// routes actually registered in groups/urls.py.

describe("client contract: settings + notifications (Task 1)", () => {
  it("getSettings issues GET /settings/profile/", async () => {
    const { userAPI } = await loadApi();

    await userAPI.getSettings();

    expect(seen[0].url).toBe("/settings/profile/");
    expect(seen[0].method).toBe("get");
  });

  it("updateSettings issues PUT /settings/profile/ with the payload", async () => {
    const { userAPI } = await loadApi();

    await userAPI.updateSettings({ bio: "hello", email_alerts: false });

    expect(seen[0].url).toBe("/settings/profile/");
    expect(seen[0].method).toBe("put");
    expect(JSON.parse(seen[0].data)).toEqual({ bio: "hello", email_alerts: false });
  });

  it("sendTestEmail issues POST /settings/email/", async () => {
    const { userAPI } = await loadApi();

    await userAPI.sendTestEmail();

    expect(seen[0].url).toBe("/settings/email/");
    expect(seen[0].method).toBe("post");
  });

  it("getNotifications issues GET /notifications/", async () => {
    const { userAPI } = await loadApi();

    await userAPI.getNotifications();

    expect(seen[0].url).toBe("/notifications/");
    expect(seen[0].method).toBe("get");
  });

  it("markAllNotificationsRead issues PUT /notifications/", async () => {
    // Collection-level, not per-id: the backend marks every unread
    // notification read in one call. The removed markNotificationRead
    // assumed a per-id endpoint that does not exist.
    const { userAPI } = await loadApi();

    await userAPI.markAllNotificationsRead();

    expect(seen[0].url).toBe("/notifications/");
    expect(seen[0].method).toBe("put");
  });

  it("carries auth and the CSRF sentinel like every other call", async () => {
    const { userAPI, setAccessToken } = await loadApi();
    setAccessToken("tok-settings");

    await userAPI.getSettings();

    expect(seen[0].headers.Authorization).toBe("Bearer tok-settings");
    expect(seen[0].headers["X-SparkLM-Client"]).toBe("web");
  });

  it("no longer exposes methods that addressed non-existent routes", async () => {
    const { userAPI, aiAPI, scheduleAPI } = await loadApi();

    // Each of these targeted a route the backend does not serve, and each
    // had zero call sites. Re-adding one would reintroduce a silent 404.
    expect(userAPI.getAchievements).toBeUndefined();
    expect(userAPI.getStats).toBeUndefined();
    expect(userAPI.markNotificationRead).toBeUndefined();
    expect(userAPI.updateProfile).toBeUndefined(); // PATCH /user/profile/ -> 405
    expect(aiAPI.submitQuiz).toBeUndefined();
    expect(scheduleAPI.updateEvent).toBeUndefined();
    expect(scheduleAPI.deleteEvent).toBeUndefined();
  });

  it("keeps the schedule methods Task 2 will use", async () => {
    const { scheduleAPI } = await loadApi();

    expect(typeof scheduleAPI.getSchedule).toBe("function");
    expect(typeof scheduleAPI.createEvent).toBe("function");
  });
});

// ── The guard that would have caught all eight ──────────────────────────
//
// Eight client methods addressed routes the backend has never served. None
// had a call site, so nothing failed loudly enough to be noticed — and the
// three production-broken pages hand-rolled their own fetches precisely
// because the client had no correct method to call.
//
// This compares every path the client requests against the routes actually
// registered in groups/urls.py. It reads the real URLconf rather than a
// hand-maintained list, so it cannot drift from the backend.
describe("client contract: every path exists in the backend URLconf", () => {
  // __dirname is <repo>/studysphere-ai-11/src/services
  const readSource = (rel: string) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { readFileSync } = require("node:fs");
    const { join } = require("node:path");
    return readFileSync(join(__dirname, rel), "utf8");
  };

  const normalise = (p: string) =>
    p.replace(/^\//, "").split("?")[0].replace(/\$\{[^}]*\}/g, "<id>");

  it("no client path is missing from groups/urls.py", () => {
    const client = readSource("api.js");
    const urls = readSource("../../../backend/LearnLM/groups/urls.py");

    const requested = [
      ...new Set(
        [...client.matchAll(/api\.(?:get|post|put|patch|delete)\(\s*[`'"]([^`'"]+)/g)].map(
          (m) => m[1]
        )
      ),
    ];

    const routes = new Set(
      [...urls.matchAll(/path\(\s*['"]([^'"]*)/g)].map((m) =>
        m[1].replace(/<[^>]+>/g, "<id>")
      )
    );
    // DefaultRouter registrations (groups, materials) are not `path()` calls.
    [
      "groups/", "groups/<id>/", "groups/join/", "groups/<id>/leave/",
      "materials/", "materials/<id>/", "materials/<id>/download/",
    ].forEach((r) => routes.add(r));

    const missing = requested.filter((p) => !routes.has(normalise(p)));

    expect(requested.length).toBeGreaterThan(15); // guards the guard
    expect(missing).toEqual([]);
  });
});
