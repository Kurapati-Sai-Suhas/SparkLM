/**
 * The production build guard (M15b).
 *
 * WHY IT EXISTS
 *   Vercel serves HTTP 200 for the SPA shell no matter what state the backend
 *   is in. A bundle built with the wrong `VITE_API_URL` therefore deploys
 *   green and is invisible to every platform health signal, while being
 *   completely unusable. The only place to catch that is before the bundle
 *   ships.
 *
 * These test the exported predicate rather than spawning a build: a guard
 * that can only be exercised by running Vite is a guard nobody can prove
 * works, and it would never be run in CI.
 */

import { describe, expect, it } from "vitest";

import { productionConfigError } from "./productionConfig";

const GOOD = "https://sparklm-api.onrender.com";

describe("only production deployments are gated", () => {
  it("says nothing for a CI compile check", () => {
    // `npm run build` runs in CI with no deployment environment attached.
    // Failing there would block every pull request on a variable CI has no
    // reason to hold.
    expect(productionConfigError({})).toBeNull();
    expect(productionConfigError({ VITE_API_URL: "" })).toBeNull();
  });

  it("says nothing for a preview deployment", () => {
    expect(
      productionConfigError({ VERCEL_ENV: "preview", VITE_API_URL: "" })
    ).toBeNull();
  });

  it("accepts a correct production configuration", () => {
    expect(
      productionConfigError({ VERCEL_ENV: "production", VITE_API_URL: GOOD })
    ).toBeNull();
  });
});

describe("what a production build is refused for", () => {
  it("a missing API URL", () => {
    // The client falls back to http://127.0.0.1:8000, which no visitor can
    // reach — the single most likely way to ship a dead frontend.
    const error = productionConfigError({ VERCEL_ENV: "production" });

    expect(error).toMatch(/not set/i);
    expect(error).toMatch(/127\.0\.0\.1:8000/);
  });

  it.each(["http://localhost:8000", "https://127.0.0.1:8000",
           "http://0.0.0.0:8000"])("a local address (%s)", (url) => {
    const error = productionConfigError({
      VERCEL_ENV: "production", VITE_API_URL: url,
    });

    expect(error).toMatch(/this machine, not a deployed backend/i);
  });

  it("a plaintext URL, because the browser blocks mixed content silently", () => {
    const error = productionConfigError({
      VERCEL_ENV: "production",
      VITE_API_URL: "http://sparklm-api.onrender.com",
    });

    expect(error).toMatch(/https/i);
    expect(error).toMatch(/mixed content/i);
  });

  it("a trailing slash, which would request //api", () => {
    // The client appends "/api". Django answers "//api/token/" with a 404
    // that reads like a routing bug rather than a configuration typo.
    const error = productionConfigError({
      VERCEL_ENV: "production", VITE_API_URL: `${GOOD}/`,
    });

    expect(error).toMatch(/trailing|must not end/i);
  });

  it("a value that is not a URL at all", () => {
    const error = productionConfigError({
      VERCEL_ENV: "production", VITE_API_URL: "sparklm-api.onrender.com",
    });

    expect(error).toMatch(/not a valid absolute URL/i);
  });
});

describe("the message tells the operator what to do", () => {
  it("names the Vercel setting for a missing variable", () => {
    const error = productionConfigError({ VERCEL_ENV: "production" });

    expect(error).toMatch(/Environment Variables/);
    expect(error).toMatch(/Production/);
  });

  it("reports the hostname, not the whole URL, for a local address", () => {
    // Deliberately narrow. Other branches DO echo the raw value, and that is
    // fine: every VITE_* value is compiled into the public bundle, so it is
    // public by construction and cannot be a secret. An earlier version of
    // this test claimed the guard "never echoes a secret" — it passed only
    // because the localhost branch happens to run first, which is a test
    // asserting an accident rather than a guarantee.
    const error = productionConfigError({
      VERCEL_ENV: "production",
      VITE_API_URL: "http://localhost:8000?some=querystring",
    });

    expect(error).toMatch(/points at localhost/);
    expect(error).not.toMatch(/querystring/);
  });
});
