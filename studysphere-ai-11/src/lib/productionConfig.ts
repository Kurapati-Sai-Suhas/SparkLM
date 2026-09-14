/**
 * Production build configuration guard (M15b).
 *
 * WHAT THIS CATCHES
 *   A production bundle built with `VITE_API_URL` unset, or still pointing at
 *   localhost, or pointing at a stale backend. The app would deploy green and
 *   then be unable to reach any API at all — the frontend serves HTTP 200, so
 *   every platform health signal says "fine" while the site is unusable.
 *
 * WHERE IT RUNS
 *   Build time, inside `vite.config.ts`. A violation FAILS THE BUILD, so
 *   Vercel marks the deployment failed and keeps serving the previous one
 *   rather than replacing a working site with a broken bundle.
 *
 * WHY IT IS SCOPED TO PRODUCTION DEPLOYS ONLY
 *   `npm run build` runs in CI purely to prove the project compiles, with no
 *   deployment environment attached. Failing there would block every pull
 *   request on a variable CI has no reason to hold. Vercel sets
 *   `VERCEL_ENV=production` only for a real production deployment, which is
 *   precisely the moment the value has to be right.
 */

/** Hosts that are never a real backend for a deployed frontend. */
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"];

export interface BuildEnvironment {
  /** Vercel's deployment environment: "production" | "preview" | undefined. */
  VERCEL_ENV?: string;
  VITE_API_URL?: string;
}

/**
 * The reason this build must not ship, or null when it is fine.
 *
 * Pure and exported so it is testable without spawning a build — the guard
 * that only runs inside a bundler is a guard nobody can prove works.
 */
export function productionConfigError(env: BuildEnvironment): string | null {
  if (env.VERCEL_ENV !== "production") return null;

  const raw = (env.VITE_API_URL ?? "").trim();
  if (!raw) {
    return (
      "VITE_API_URL is not set for this production build. The bundle would " +
      "fall back to http://127.0.0.1:8000, which no visitor can reach. Set " +
      "it in the Vercel project's Environment Variables for the Production " +
      "environment."
    );
  }

  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return `VITE_API_URL is not a valid absolute URL: ${raw}`;
  }

  if (LOCAL_HOSTS.includes(url.hostname)) {
    return (
      `VITE_API_URL points at ${url.hostname}, which is this machine, not a ` +
      `deployed backend. A production bundle built with it cannot reach any ` +
      `API.`
    );
  }

  if (url.protocol !== "https:") {
    // The SPA is served over HTTPS, so a plaintext API is blocked as mixed
    // content by the browser — silently, with no request in the network log.
    return (
      `VITE_API_URL must be https:// for a production build (got ` +
      `${url.protocol}//). Browsers block plaintext requests from an HTTPS ` +
      `page as mixed content.`
    );
  }

  if (raw.endsWith("/")) {
    // The client appends "/api", so a trailing slash produces "//api" —
    // which Django answers with a 404 that looks like a routing bug.
    return `VITE_API_URL must not end with "/" (got ${raw}); the API client appends "/api".`;
  }

  return null;
}
