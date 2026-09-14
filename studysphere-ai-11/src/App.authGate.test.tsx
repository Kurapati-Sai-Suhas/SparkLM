/**
 * The screen a visitor sees while the app decides whether they are signed in
 * (M15b).
 *
 * THE FAILURE THIS COMES FROM
 *   SparkLM was reported as "not opening". Both services were healthy: Vercel
 *   served the shell in 0.48s and the API answered `{"status":"ok","db":
 *   "connected"}`. What was actually happening is that the API runs on
 *   Render's FREE plan and sleeps after ~15 minutes idle, and the very first
 *   thing every page load does is ask the server "am I signed in?".
 *
 *   During a cold start — 92.9s, measured by this repository's own
 *   warm-keeper — that check was unresolved, and `ProtectedRoute` rendered a
 *   near-black full-screen page with a bare spinner and no text at all. A
 *   minute and a half of that is indistinguishable from a dead site.
 *
 *   The words are the fix. The bounded wait is the axios timeout; when it
 *   expires the gate resolves to "signed out" and the login page renders, so
 *   the worst case became a slow arrival rather than a dead one.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

vi.mock("@/services/api", () => ({
  bootstrapAuth: vi.fn(),
  getAccessToken: vi.fn(() => null),
  setAccessToken: vi.fn(),
  resetAuthBootstrap: vi.fn(),
  authAPI: { login: vi.fn(), signup: vi.fn(), googleLogin: vi.fn() },
}));

vi.mock("@react-oauth/google", () => ({
  GoogleLogin: () => null,
  GoogleOAuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import { bootstrapAuth, getAccessToken } from "@/services/api";

/**
 * The gate, extracted exactly as `App.tsx` defines it.
 *
 * Rendering the whole `App` would drag in the router, every lazy route and
 * the query client — and would test those, not this. The component under test
 * is the one that decides what a waiting visitor looks at.
 */
async function renderGate() {
  const { default: React, useEffect, useState } = await import("react");
  const { MemoryRouter, Navigate } = await import("react-router-dom");

  const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
    const [status, setStatus] = useState<"checking" | "in" | "out">(
      getAccessToken() ? "in" : "checking"
    );
    useEffect(() => {
      if (status !== "checking") return;
      let cancelled = false;
      bootstrapAuth().then((ok: boolean) => {
        if (!cancelled) setStatus(ok ? "in" : "out");
      });
      return () => {
        cancelled = true;
      };
    }, [status]);

    if (status === "checking") {
      return (
        <div
          data-testid="auth-checking"
          className="flex h-screen flex-col items-center justify-center gap-4 bg-slate-950 px-6 text-center"
        >
          <div className="h-10 w-10 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin" />
          <p className="text-sm text-slate-300">Signing you in…</p>
          <p className="max-w-xs text-xs text-slate-500">
            Waking the server if it has been idle — this can take up to a minute.
          </p>
        </div>
      );
    }
    if (status === "out") return <Navigate to="/auth" replace />;
    return <>{children}</>;
  };

  render(
    <MemoryRouter initialEntries={["/"]}>
      <ProtectedRoute>
        <div data-testid="dashboard">dashboard</div>
      </ProtectedRoute>
    </MemoryRouter>
  );
}

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

describe("while the auth check is in flight", () => {
  it("explains what it is doing instead of showing a bare spinner", async () => {
    vi.mocked(bootstrapAuth).mockReturnValue(new Promise(() => {}) as any);

    await renderGate();

    expect(await screen.findByTestId("auth-checking")).toBeInTheDocument();
    expect(screen.getByText(/signing you in/i)).toBeInTheDocument();
  });

  it("warns that a sleeping server can take a minute", async () => {
    // The API is on a free plan that sleeps. Without saying so, a 90s wait
    // reads as a broken site — which is exactly how it was reported.
    vi.mocked(bootstrapAuth).mockReturnValue(new Promise(() => {}) as any);

    await renderGate();

    expect(await screen.findByText(/can take up to a minute/i))
      .toBeInTheDocument();
  });

  it("does not show the dashboard before the answer arrives", async () => {
    vi.mocked(bootstrapAuth).mockReturnValue(new Promise(() => {}) as any);

    await renderGate();

    expect(screen.queryByTestId("dashboard")).not.toBeInTheDocument();
  });
});

describe("once the auth check resolves", () => {
  it("shows the app when a session was recovered", async () => {
    vi.mocked(bootstrapAuth).mockResolvedValue(true as any);

    await renderGate();

    expect(await screen.findByTestId("dashboard")).toBeInTheDocument();
  });

  it("leaves the loading screen when there is no session", async () => {
    // The important half: a FAILED check — including one that failed because
    // the request timed out against a sleeping server — must land the visitor
    // somewhere usable, not leave them on the loading screen.
    vi.mocked(bootstrapAuth).mockResolvedValue(false as any);

    await renderGate();

    await waitFor(() =>
      expect(screen.queryByTestId("auth-checking")).not.toBeInTheDocument()
    );
    expect(screen.queryByTestId("dashboard")).not.toBeInTheDocument();
  });

  it("does not block at all when a token is already in memory", async () => {
    vi.mocked(getAccessToken).mockReturnValue("token" as any);

    await renderGate();

    expect(screen.getByTestId("dashboard")).toBeInTheDocument();
    expect(bootstrapAuth).not.toHaveBeenCalled();
  });
});

describe("the real component carries these words", () => {
  it("App.tsx renders the explanation, not just a spinner", async () => {
    // Guards against the extracted copy above drifting from the component it
    // mirrors: if someone deletes the text from App.tsx, the tests above
    // would keep passing against their own copy.
    const fs = await import("node:fs/promises");
    const path = await import("node:path");
    const source = await fs.readFile(
      path.resolve(process.cwd(), "src/App.tsx"), "utf-8");

    expect(source).toMatch(/Signing you in/);
    expect(source).toMatch(/can take up to a minute/);
    expect(source).toMatch(/data-testid="auth-checking"/);
  });
});
