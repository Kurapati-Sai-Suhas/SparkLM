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
  it("attaches the stored token as a Bearer header", async () => {
    localStorage.setItem("authToken", "tok-123");
    const { userAPI } = await loadApi();

    await userAPI.getProfile();

    expect(seen[0].headers.Authorization).toBe("Bearer tok-123");
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
    const { userAPI } = await loadApi();

    const res = await userAPI.getProfile();

    expect(res.data.username).toBe("sam");
    expect(localStorage.getItem("authToken")).toBe("fresh-token");
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

  it("clears storage and redirects when there is no refresh token", async () => {
    localStorage.setItem("authToken", "expired");
    responses = { "/user/profile/": [reply(401)] };
    const { userAPI } = await loadApi();

    await expect(userAPI.getProfile()).rejects.toBeTruthy();

    expect(localStorage.getItem("authToken")).toBeNull();
    expect(window.location.href).toBe("/auth");
    expect(seen.filter((r) => r.url === "/token/refresh/")).toHaveLength(0);
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
    const { authAPI } = await loadApi();

    await authAPI.login("sam", "correct");

    expect(localStorage.getItem("authToken")).toBe("a-tok");
    expect(localStorage.getItem("refreshToken")).toBe("r-tok");
  });

  it("stores both tokens on Google sign-in", async () => {
    responses = { "/auth/google/": [reply(200, { access: "g-tok", refresh: "gr-tok" })] };
    const { authAPI } = await loadApi();

    await authAPI.googleLogin("google-credential");

    expect(localStorage.getItem("authToken")).toBe("g-tok");
    expect(localStorage.getItem("refreshToken")).toBe("gr-tok");
  });

  it("clears both tokens on logout", async () => {
    localStorage.setItem("authToken", "a");
    localStorage.setItem("refreshToken", "r");
    const { authAPI } = await loadApi();

    authAPI.logout();

    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("refreshToken")).toBeNull();
  });
});
