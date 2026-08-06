/**
 * Schedule page — data flow through the shared API client
 * (N3 Phase 1a, Task 2).
 *
 * This page fetched the RELATIVE path "/api/schedule/". On Vercel that
 * resolves against the SPA's own origin, where the catch-all rewrite in
 * vercel.json serves index.html with HTTP 200 — so the page parsed the
 * app's own HTML as JSON, threw, and swallowed it in `.catch(console.error)`.
 * It has never shown real data in production, and nothing reported it.
 *
 * These assert BEHAVIOUR a user can observe — sessions render, a submission
 * is sent, a failure is visible. The one structural assertion is that the
 * request leaves through the shared client, because that is the whole point
 * of the task: it is what supplies the baseURL, the auth header and the 401
 * refresh interceptor a raw fetch never received.
 *
 * Queried by placeholder/role rather than data-testid so no production
 * markup changes for the tests' benefit. `fireEvent` rather than
 * `user-event`, which is not a dependency of this project.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const getSchedule = vi.fn();
const createEvent = vi.fn();

vi.mock("@/services/api", () => ({
  scheduleAPI: {
    getSchedule: (...a: any[]) => getSchedule(...a),
    createEvent: (...a: any[]) => createEvent(...a),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...a: any[]) => toastSuccess(...a),
    error: (...a: any[]) => toastError(...a),
  },
}));

import Schedule from "./Schedule";

const TITLE_PLACEHOLDER = "e.g. Master DP Algorithms";

beforeEach(() => {
  vi.clearAllMocks();
  getSchedule.mockResolvedValue({ data: [] });
  createEvent.mockResolvedValue({ data: { status: "success", id: 1 } });
});

afterEach(() => cleanup());

/** Opens the form, fills both fields, and clicks Save. */
function scheduleSession(title = "Algorithms", time = "09:30") {
  fireEvent.click(screen.getByTestId("new-session-btn"));
  fireEvent.change(screen.getByPlaceholderText(TITLE_PLACEHOLDER), {
    target: { value: title },
  });
  const timeInput = document.querySelector('input[type="time"]') as HTMLInputElement;
  fireEvent.change(timeInput, { target: { value: time } });
  fireEvent.click(screen.getByText("Save Session"));
}

describe("Schedule — loading existing sessions", () => {
  it("renders sessions returned by the API", async () => {
    getSchedule.mockResolvedValue({
      data: [
        { id: 1, title: "Graph theory revision", start_time: "2026-09-01T10:00:00" },
        { id: 2, title: "Mock interview", start_time: "2026-09-02T14:30:00" },
      ],
    });

    render(<Schedule />);

    expect(await screen.findByText("Graph theory revision")).toBeTruthy();
    expect(screen.getByText("Mock interview")).toBeTruthy();
  });

  it("requests the schedule through the shared client", async () => {
    render(<Schedule />);

    await waitFor(() => expect(getSchedule).toHaveBeenCalledTimes(1));
  });

  it("survives a non-array payload without crashing", async () => {
    // The guard the original code had: a paginated or error body must not
    // put a non-iterable into state, because the list is rendered with
    // events.map().
    //
    // Asserting the page container is NOT enough — it resolves on the first
    // render, before the async state update lands, so it passes even with
    // the guard removed. Waiting for the empty-state branch forces the
    // post-update render, which is where an unguarded payload throws.
    // Mutation-verified: dropping the isArray guard fails this.
    getSchedule.mockResolvedValue({ data: { detail: "unexpected" } });

    render(<Schedule />);

    await waitFor(() => expect(getSchedule).toHaveBeenCalled());
    expect(await screen.findByTestId("empty-schedule-cta")).toBeTruthy();
  });

  it("still renders when loading fails", async () => {
    getSchedule.mockRejectedValue(new Error("network down"));

    render(<Schedule />);

    await waitFor(() => expect(getSchedule).toHaveBeenCalled());
    expect(await screen.findByTestId("empty-schedule-cta")).toBeTruthy();
  });
});

describe("Schedule — creating a session", () => {
  it("sends title and start_time through the shared client", async () => {
    render(<Schedule />);

    scheduleSession("Algorithms", "09:30");

    await waitFor(() => expect(createEvent).toHaveBeenCalledTimes(1));
    const payload = createEvent.mock.calls[0][0];
    expect(payload.title).toBe("Algorithms");
    // Backend expects "<date>T<HH:MM>:00", composed from the selected
    // calendar date and the time input.
    expect(payload.start_time).toMatch(/^\d{4}-\d{2}-\d{2}T09:30:00$/);
  });

  it("shows the new session without a reload", async () => {
    render(<Schedule />);

    scheduleSession("Algorithms", "09:30");

    expect(await screen.findByText("Algorithms")).toBeTruthy();
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("tells the user when scheduling fails", async () => {
    // The write-path regression this task closes. `fetch` resolved on a 500,
    // so the old code did nothing at all — no toast, no error, the form just
    // sat open. axios rejects, and the new catch turns that into feedback
    // rather than an unhandled rejection.
    createEvent.mockRejectedValue(new Error("500"));

    render(<Schedule />);
    scheduleSession();

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("does not submit an incomplete form", async () => {
    render(<Schedule />);
    fireEvent.click(screen.getByTestId("new-session-btn"));

    fireEvent.click(screen.getByText("Save Session"));

    expect(createEvent).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalled();
  });

  it("leaves the list unchanged when the API reports a non-success status", async () => {
    // A 200 whose body is not {status:"success"} must not fabricate a row.
    createEvent.mockResolvedValue({ data: { status: "error" } });

    render(<Schedule />);
    scheduleSession("Ghost session");

    await waitFor(() => expect(createEvent).toHaveBeenCalled());
    expect(screen.queryByText("Ghost session")).toBeNull();
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
