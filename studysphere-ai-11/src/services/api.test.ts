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
    seen.push({ url: config.url, headers: { ...config.headers } });
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
      "/user/stats/": [reply(401), reply(200, {})],
      "/token/refresh/": [reply(200, { access: "fresh-token" })],
    };
    const { userAPI } = await loadApi();

    await Promise.all([
      userAPI.getProfile(),
      userAPI.getDashboardStats(),
      userAPI.getStats(),
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
