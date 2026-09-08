/**
 * The login form must never be a silent dead end (M15).
 *
 * REPORTED: submit the login form in production, and the page stays on the
 * login page — no success, no error, no redirect.
 *
 * MEASURED against the deployed system: the backend is healthy
 * (`/api/health/` -> `{"status":"ok","db":"connected"}`), CORS is correct for
 * the real Vercel origin, and invalid credentials return 401 with a JSON body
 * and DO render a visible message. So the reported symptom is not the
 * invalid-credential path.
 *
 * What the form actually lacked:
 *
 *   - no loading state, so an in-flight request looked exactly like a form
 *     that had ignored the click. The API is on Render's FREE plan, which
 *     sleeps when idle and then runs `migrate` and `ensure_submission_
 *     partitions` before accepting a connection — a cold start legitimately
 *     takes tens of seconds.
 *   - no timeout, so a hung request never terminated and the UI stayed inert
 *     indefinitely.
 *   - no duplicate-submit guard, so waiting users clicked repeatedly against
 *     a rate-limited endpoint.
 *   - one error message for every failure, wrongly telling a user with a
 *     correct password to check their password.
 *   - `if (response.access)` with no else: a 200 carrying no token fell off
 *     the end of the `try` having set no error and performed no navigation.
 *
 * These tests drive the REAL component and the REAL handler. Only the network
 * boundary is stubbed — mocking the handler would test the mock.
 */

import { cleanup, fireEvent, render, screen, waitFor }
  from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import Auth, { loginErrorMessage } from "./Auth";
import { authAPI } from "@/services/api";

vi.mock("@/services/api", () => ({
  authAPI: {
    login: vi.fn(),
    signup: vi.fn(),
    googleLogin: vi.fn(),
  },
}));

vi.mock("@react-oauth/google", () => ({
  GoogleLogin: () => null,
}));

function axiosError(status?: number, code?: string) {
  const error: any = new Error(code ?? `status ${status}`);
  error.isAxiosError = true;
  if (code) error.code = code;
  if (status !== undefined) {
    error.response = { status, data: { detail: "irrelevant" } };
  }
  return error;
}

function renderAuth() {
  render(
    <MemoryRouter>
      <Auth />
    </MemoryRouter>
  );
}

async function signIn(username = "learner", password = "correct horse") {
  renderAuth();
  fireEvent.change(screen.getByPlaceholderText("Enter your username"), {
    target: { value: username },
  });
  fireEvent.change(screen.getByPlaceholderText("••••••••"), {
    target: { value: password },
  });
  const button = screen.getByRole("button", { name: /sign in/i });
  fireEvent.click(button);
  return { button };
}

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom refuses a real navigation; the handler only sets href.
  Object.defineProperty(window, "location", {
    writable: true,
    value: { href: "http://localhost/auth" },
  });
});

// ─────────────────────────────────────────────────────────────
// The failure the milestone exists to remove
// ─────────────────────────────────────────────────────────────

describe("the form is never a silent dead end", () => {
  it("shows an error when the server returns 200 with no access token", async () => {
    // THE regression. The old handler set no error and did not navigate:
    // the user saw the login page, unchanged, with no explanation.
    vi.mocked(authAPI.login).mockResolvedValue({} as any);

    await signIn();

    expect(await screen.findByRole("alert")).toHaveTextContent(/sign in failed/i);
    expect(window.location.href).toBe("http://localhost/auth");
  });

  it("re-enables the button after that failure so the user can retry", async () => {
    vi.mocked(authAPI.login).mockResolvedValue({} as any);

    const { button } = await signIn();

    await waitFor(() => expect(button).not.toBeDisabled());
  });
});

// ─────────────────────────────────────────────────────────────
// Every failure mode says something DIFFERENT and true
// ─────────────────────────────────────────────────────────────

describe("error messages distinguish the cause", () => {
  it("401 blames the credentials", async () => {
    vi.mocked(authAPI.login).mockRejectedValue(axiosError(401));

    await signIn();

    expect(await screen.findByRole("alert"))
      .toHaveTextContent(/incorrect username or password/i);
  });

  it("a network failure does NOT blame the credentials", async () => {
    // The old message sent a user with a perfectly good password to check
    // their password, which is worse than saying nothing.
    vi.mocked(authAPI.login).mockRejectedValue(axiosError(undefined));

    await signIn();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not reach the server/i);
    expect(alert).not.toHaveTextContent(/password/i);
  });

  it("a timeout says the server was slow, and suggests retrying", async () => {
    vi.mocked(authAPI.login).mockRejectedValue(
      axiosError(undefined, "ECONNABORTED")
    );

    await signIn();

    expect(await screen.findByRole("alert"))
      .toHaveTextContent(/took too long/i);
  });

  it("a rate limit tells the user to wait rather than to retype", async () => {
    vi.mocked(authAPI.login).mockRejectedValue(axiosError(429));

    await signIn();

    expect(await screen.findByRole("alert"))
      .toHaveTextContent(/too many attempts/i);
  });

  it("a server error is not blamed on the user", async () => {
    vi.mocked(authAPI.login).mockRejectedValue(axiosError(503));

    await signIn();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/server had a problem/i);
    expect(alert).not.toHaveTextContent(/password/i);
  });

  it("never renders anything the server said", async () => {
    // The one screen an unauthenticated stranger can reach. A fixed set of
    // strings cannot leak a stack trace, a database error or a secret.
    const leaky: any = new Error("boom");
    leaky.isAxiosError = true;
    leaky.response = {
      status: 500,
      data: { detail: "psycopg2.OperationalError: FATAL password authentication failed for user sparklm" },
    };
    vi.mocked(authAPI.login).mockRejectedValue(leaky);

    await signIn();

    const alert = await screen.findByRole("alert");
    expect(alert).not.toHaveTextContent(/psycopg2/i);
    expect(alert).not.toHaveTextContent(/FATAL/i);
    expect(alert).not.toHaveTextContent(/sparklm/i);
  });
});

describe("the login handler logs nothing sensitive", () => {
  it("does not write the axios error to the console", async () => {
    // The old handler did `console.error("LOGIN FAILED...", error)`. An axios
    // error carries `config.data` — the serialised REQUEST BODY — so every
    // failed sign-in printed the user's password into the browser console,
    // where any extension or shoulder-surfer could read it. Observed in the
    // production console during this milestone's diagnosis.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const leaky: any = axiosError(401);
    leaky.config = { data: JSON.stringify({ username: "learner", password: "hunter2" }) };
    vi.mocked(authAPI.login).mockRejectedValue(leaky);

    await signIn("learner", "hunter2");
    await screen.findByRole("alert");

    const printed = spy.mock.calls.flat().map((a) => JSON.stringify(a)).join(" ");
    expect(printed).not.toMatch(/hunter2/);
    spy.mockRestore();
  });
});

describe("loginErrorMessage covers each branch directly", () => {
  it.each([
    [401, /incorrect username or password/i],
    [400, /incorrect username or password/i],
    [429, /too many attempts/i],
    [500, /server had a problem/i],
    [502, /server had a problem/i],
  ])("status %i", (status, pattern) => {
    expect(loginErrorMessage(axiosError(status as number))).toMatch(pattern);
  });

  it("a response that is present but unclassified still says something", () => {
    expect(loginErrorMessage(axiosError(418))).toMatch(/sign in failed/i);
  });
});

// ─────────────────────────────────────────────────────────────
// Loading state and duplicate submission
// ─────────────────────────────────────────────────────────────

describe("loading state", () => {
  it("disables the button and says so while the request is in flight", async () => {
    let release: (value: any) => void = () => {};
    vi.mocked(authAPI.login).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }) as any
    );

    const { button } = await signIn();

    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText(/signing in/i)).toBeInTheDocument();

    release({ access: "token" });
  });

  it("warns that a sleeping server can take a minute", async () => {
    // The API is on Render's free plan. Without this the user is staring at
    // an unexplained pause for the whole cold start.
    vi.mocked(authAPI.login).mockReturnValue(new Promise(() => {}) as any);

    await signIn();

    expect(await screen.findByText(/can take up to a minute/i))
      .toBeInTheDocument();
  });

  it("clears the loading state after a failure", async () => {
    vi.mocked(authAPI.login).mockRejectedValue(axiosError(401));

    const { button } = await signIn();

    await waitFor(() => expect(button).not.toBeDisabled());
    expect(screen.queryByText(/signing in/i)).not.toBeInTheDocument();
  });

  it("a second submit while one is in flight does not fire a second request",
    async () => {
      vi.mocked(authAPI.login).mockReturnValue(new Promise(() => {}) as any);

      const { button } = await signIn();
      fireEvent.click(button);
      fireEvent.click(button);

      expect(authAPI.login).toHaveBeenCalledTimes(1);
    });
});

// ─────────────────────────────────────────────────────────────
// Success
// ─────────────────────────────────────────────────────────────

describe("successful login", () => {
  it("navigates to the dashboard", async () => {
    vi.mocked(authAPI.login).mockResolvedValue({
      access: "access-token", refresh: "refresh-token",
    } as any);

    await signIn();

    await waitFor(() => expect(window.location.href).toBe("/"));
  });

  it("passes the typed credentials through to the API client unchanged", async () => {
    vi.mocked(authAPI.login).mockResolvedValue({ access: "t" } as any);

    await signIn("learner", "correct horse");

    expect(authAPI.login).toHaveBeenCalledWith("learner", "correct horse");
  });

  it("shows no error on the way out", async () => {
    vi.mocked(authAPI.login).mockResolvedValue({ access: "t" } as any);

    await signIn();

    await waitFor(() => expect(window.location.href).toBe("/"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays disabled after success so a navigating page cannot be re-submitted",
    async () => {
      vi.mocked(authAPI.login).mockResolvedValue({ access: "t" } as any);

      const { button } = await signIn();

      await waitFor(() => expect(window.location.href).toBe("/"));
      expect(button).toBeDisabled();
    });
});

// ─────────────────────────────────────────────────────────────
// Validation that existed before, still working
// ─────────────────────────────────────────────────────────────

describe("client-side validation", () => {
  it("does not call the API with an empty username", async () => {
    renderAuth();
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "something" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(authAPI.login).not.toHaveBeenCalled();
  });
});
