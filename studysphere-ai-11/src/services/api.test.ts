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
      // `params` added for the P2.1 pagination tests, which assert which page
      // numbers were requested. Existing assertions read only url/method/
      // data/headers, so this is additive.
      params: config.params,
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
    // Retired with the Flashcards surface in M1/P1.2-B. The backend route
    // /api/ai/flashcards/ no longer exists, so re-adding this method would
    // point the client at a 404 — the same class of dead method the five
    // above document.
    expect(aiAPI.generateFlashcards).toBeUndefined();
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

    // `fetchAllPages` is matched alongside api.<verb>: it is a wrapper over
    // api.get that walks the DRF page chain (M2 P2.1), so its literal is the
    // path that reaches the network and belongs under this contract.
    const requested = [
      ...new Set(
        [
          ...client.matchAll(
            /(?:api\.(?:get|post|put|patch|delete)|fetchAllPages)\(\s*[`'"]([^`'"]+)/g
          ),
        ].map((m) => m[1])
      ),
    ];

    const routes = new Set(
      [...urls.matchAll(/path\(\s*['"]([^'"]*)/g)]
        .map((m) => m[1].replace(/<[^>]+>/g, "<id>"))
        // `path('', include(router.urls))` parses to an EMPTY route, which
        // would match any client path normalising to "" — verified: without
        // this filter, a client path of "/" passes the check. The router's
        // real routes are listed explicitly below.
        .filter(Boolean)
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

  it("sees every api.<verb>() call — no path is invisible to the guard", () => {
    // The check above can only inspect paths written as string literals. A
    // call like `api.get(url)` is skipped SILENTLY, so the contract would
    // pass while covering less than it claims. Comparing call sites to
    // captured paths turns that blind spot into a failure.
    const client = readSource("api.js");

    const callSites = [
      ...client.matchAll(/api\.(?:get|post|put|patch|delete)\(/g),
    ].length;
    const captured = [
      ...client.matchAll(/api\.(?:get|post|put|patch|delete)\(\s*[`'"][^`'"]+/g),
    ].length;

    // Exactly one call takes a variable path: fetchAllPages re-issues the
    // caller's own path with a page number (M2 P2.1). Its literals are
    // checked at the fetchAllPages(...) call sites by the test above, which
    // matches that helper by name — so the contract still covers them. Any
    // OTHER variable-path call is a genuine blind spot and fails here.
    const viaPaginationHelper = [
      ...client.matchAll(/await api\.get\(path, \{/g),
    ].length;
    expect(viaPaginationHelper).toBe(1);

    expect(captured + viaPaginationHelper).toBe(callSites);
  });
});

describe("fetchAllPages — paginated list reads (M2 P2.1)", () => {
  /** Queue `pages` responses for `url`, the last one ending the chain. */
  function paginated(url: string, pages: any[][]) {
    responses[url] = pages.map((results, i) => reply(200, {
      count: pages.flat().length,
      next: i === pages.length - 1 ? null : `http://api.example/x?page=${i + 2}`,
      previous: i === 0 ? null : `http://api.example/x?page=${i}`,
      results,
    }));
  }

  it("returns every row across pages, not just the first page", async () => {
    const { fetchAllPages } = await loadApi();
    paginated("/materials/", [
      [{ id: 1 }, { id: 2 }],
      [{ id: 3 }, { id: 4 }],
      [{ id: 5 }],
    ]);

    const res = await fetchAllPages("/materials/");

    expect(res.data.results.map((r: any) => r.id)).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps the { count, next, previous, results } envelope the pages read", async () => {
    // Five files do `res.data.results || res.data`. If this helper returned a
    // bare array, every one of them would still "work" via the fallback and
    // silently change shape for anything reading `count`.
    const { fetchAllPages } = await loadApi();
    paginated("/materials/", [[{ id: 1 }], [{ id: 2 }]]);

    const res = await fetchAllPages("/materials/");

    expect(Object.keys(res.data).sort()).toEqual(["count", "next", "previous", "results"]);
    expect(res.data.count).toBe(2);
    expect(res.data.next).toBeNull();
  });

  it("pages by number and never requests the absolute URL from `next`", async () => {
    // DRF builds `next` from the request host, which behind a proxy can come
    // back as http:// — following it would be a mixed-content request from an
    // https page, and would bypass the client's baseURL and interceptors.
    const { fetchAllPages } = await loadApi();
    paginated("/materials/", [[{ id: 1 }], [{ id: 2 }], [{ id: 3 }]]);

    await fetchAllPages("/materials/");

    const calls = seen.filter((s) => s.url === "/materials/");
    expect(calls.map((c) => c.params.page)).toEqual([1, 2, 3]);
    expect(seen.some((s) => String(s.url).startsWith("http"))).toBe(false);
  });

  it("stops after one request when there is no next page", async () => {
    const { fetchAllPages } = await loadApi();
    paginated("/groups/", [[{ id: 1 }, { id: 2 }]]);

    await fetchAllPages("/groups/");

    expect(seen.filter((s) => s.url === "/groups/")).toHaveLength(1);
  });

  it("preserves caller-supplied params alongside the page number", async () => {
    const { fetchAllPages } = await loadApi();
    paginated("/materials/", [[{ id: 1 }]]);

    await fetchAllPages("/materials/", { params: { study_group: 7 } });

    expect(seen[0].params).toEqual({ study_group: 7, page: 1 });
  });

  it("hands back an unpaginated response untouched", async () => {
    // Not every list endpoint is paginated. Wrapping a bare array in an
    // invented envelope would break callers that read the array directly.
    const { fetchAllPages } = await loadApi();
    responses["/plain/"] = [reply(200, [{ id: 1 }, { id: 2 }])];

    const res = await fetchAllPages("/plain/");

    expect(res.data).toEqual([{ id: 1 }, { id: 2 }]);
    expect(seen.filter((s) => s.url === "/plain/")).toHaveLength(1);
  });

  it("stops rather than looping forever if `next` never clears", async () => {
    // A backend bug that always returns a next link must not hang the tab.
    const { fetchAllPages } = await loadApi();
    responses["/loop/"] = [reply(200, {
      count: 999, next: "http://api.example/x?page=2", previous: null, results: [{ id: 1 }],
    })];

    await expect(fetchAllPages("/loop/")).rejects.toThrow(/more pages after 50/);

    expect(seen.filter((s) => s.url === "/loop/")).toHaveLength(50);
  });

  it("THROWS rather than silently returning a partial list at the cap", async () => {
    // The defect this helper exists to prevent, reappearing at a larger scale.
    // Before this was enforced: a server holding 3000 rows returned 2500 with
    // `count: 2500` and `next: null` — an envelope asserting completeness
    // about a list missing 500 rows, undetectable by any caller.
    //
    // Returning the partial rows is NOT an acceptable fallback. Callers read
    // `res.data.results` and render it; there is no field they check that
    // would tell them the list is short. A thrown error reaches their existing
    // catch blocks and is visible; a truncated list is not.
    const { fetchAllPages } = await loadApi();
    responses["/materials/"] = [reply(200, {
      count: 3000,
      next: "http://api.example/x?page=2",
      previous: null,
      results: Array.from({ length: 50 }, (_, i) => ({ id: i })),
    })];

    await expect(fetchAllPages("/materials/")).rejects.toThrow(
      /collected 2500 of 3000/
    );
  });

  it("does not throw when the last page lands exactly on the cap", async () => {
    // Off-by-one guard: 50 pages is allowed, 51 is not. Without this, the
    // fix above would reject a dataset it can serve perfectly well.
    const { fetchAllPages } = await loadApi();
    responses["/materials/"] = Array.from({ length: 50 }, (_, i) => reply(200, {
      count: 500,
      next: i === 49 ? null : `http://api.example/x?page=${i + 2}`,
      previous: i === 0 ? null : "http://api.example/x",
      results: Array.from({ length: 10 }, (_, j) => ({ id: i * 10 + j })),
    }));

    const res = await fetchAllPages("/materials/");

    expect(res.data.results).toHaveLength(500);
    expect(seen.filter((s) => s.url === "/materials/")).toHaveLength(50);
  });

  it("returns an empty envelope for an empty list, not a throw", async () => {
    // Empty states must survive: callers do `res.data.results || res.data || []`
    // and render an empty-state panel.
    const { fetchAllPages } = await loadApi();
    responses["/materials/"] = [reply(200, {
      count: 0, next: null, previous: null, results: [],
    })];

    const res = await fetchAllPages("/materials/");

    expect(res.data.results).toEqual([]);
    expect(res.data.count).toBe(0);
    expect(seen.filter((s) => s.url === "/materials/")).toHaveLength(1);
  });

  it("propagates errors instead of returning a partial list as if complete", async () => {
    const { fetchAllPages } = await loadApi();
    responses["/materials/"] = [
      reply(200, { count: 4, next: "http://api.example/x?page=2", previous: null, results: [{ id: 1 }] }),
      reply(500, { detail: "boom" }),
    ];

    await expect(fetchAllPages("/materials/")).rejects.toThrow();
  });

  it("routes the four truncating list reads through it", async () => {
    // The regression this phase exists to prevent: a list read that pages
    // once and drops the rest. Each of these was doing exactly that.
    const { groupsAPI } = await loadApi();
    paginated("/groups/", [[{ id: 1 }], [{ id: 2 }]]);
    paginated("/materials/?study_group=3", [[{ id: 1 }], [{ id: 2 }]]);
    paginated("/groups/3/members/", [[{ id: 1 }], [{ id: 2 }]]);
    paginated("/quizzes/assigned/?study_group=3", [[{ id: 1 }], [{ id: 2 }]]);

    const results = await Promise.all([
      groupsAPI.getAll(),
      groupsAPI.getMaterials(3),
      groupsAPI.getMembers(3),
      groupsAPI.getAssignedQuizzes(3),
    ]);

    for (const res of results) {
      expect(res.data.results).toHaveLength(2);
    }
  });
});
