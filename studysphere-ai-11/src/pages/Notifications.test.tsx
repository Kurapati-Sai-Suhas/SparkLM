/**
 * Notifications page — data flow through the shared API client
 * (N3 Phase 1a, Task 3).
 *
 * This page fetched the RELATIVE path "/api/notifications/". On Vercel that
 * resolves against the SPA's own origin, where the catch-all rewrite in
 * vercel.json serves index.html with HTTP 200 — so the page parsed the
 * app's own HTML as JSON, threw, and swallowed it in `.catch(console.error)`.
 * It has never shown a real notification in production.
 *
 * The mark-all-read path carried a second, quieter defect: `fetch` resolves
 * on ANY status, so a failed PUT still ran the `.then` and marked everything
 * read locally. The UI claimed a success the server never performed. axios
 * rejects on non-2xx, so that lie is now impossible — pinned below.
 *
 * Queried by visible text rather than data-testid, so no production markup
 * changes for the tests' benefit.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const getNotifications = vi.fn();
const markAllNotificationsRead = vi.fn();

vi.mock("@/services/api", () => ({
  userAPI: {
    getNotifications: (...a: any[]) => getNotifications(...a),
    markAllNotificationsRead: (...a: any[]) => markAllNotificationsRead(...a),
  },
}));

import Notifications from "./Notifications";

const TWO_UNREAD = [
  { id: 1, title: "Quiz graded", description: "Arrays quiz scored 80%", time: "2h", is_read: false },
  { id: 2, title: "New material", description: "Lecture 4 uploaded", time: "5h", is_read: false },
];

beforeEach(() => {
  vi.clearAllMocks();
  getNotifications.mockResolvedValue({ data: [] });
  markAllNotificationsRead.mockResolvedValue({ data: { status: "success" } });
});

afterEach(() => cleanup());

describe("Notifications — loading", () => {
  it("renders notifications returned by the API", async () => {
    getNotifications.mockResolvedValue({ data: TWO_UNREAD });

    render(<Notifications />);

    expect(await screen.findByText("Quiz graded")).toBeTruthy();
    expect(screen.getByText("Lecture 4 uploaded")).toBeTruthy();
  });

  it("requests notifications through the shared client, once", async () => {
    // The transport IS the fix: a raw fetch bypassed baseURL, the auth
    // header and the 401 refresh interceptor, which is how this page came
    // to request the SPA's own origin and parse HTML as JSON.
    render(<Notifications />);

    await waitFor(() => expect(getNotifications).toHaveBeenCalledTimes(1));
  });

  it("maps is_read onto the read flag used for the unread count", async () => {
    // The backend field is `is_read`; the component renders `read`. If the
    // mapping is dropped, every notification silently counts as unread.
    getNotifications.mockResolvedValue({
      data: [
        { ...TWO_UNREAD[0], is_read: true },
        { ...TWO_UNREAD[1], is_read: false },
      ],
    });

    render(<Notifications />);

    expect(await screen.findByText("1 new")).toBeTruthy();
  });

  it("survives a non-array payload WITHOUT relying on the catch", async () => {
    // Asserting only that the empty state renders is not enough, and
    // mutation testing proved it: with the guard removed, `data.map` throws
    // a TypeError that `.catch(console.error)` absorbs, leaving the list
    // empty and the empty state showing. The test passed while the guard
    // was gone.
    //
    // The distinguishing signal is console.error. The guard means the bad
    // payload is IGNORED; no guard means it THROWS and is swallowed. So a
    // clean console is what proves the guard, not the rendered output.
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    getNotifications.mockResolvedValue({ data: { detail: "unexpected" } });

    render(<Notifications />);

    await waitFor(() => expect(getNotifications).toHaveBeenCalled());
    expect(await screen.findByText(/all caught up/i)).toBeTruthy();
    expect(onError).not.toHaveBeenCalled();
    onError.mockRestore();
  });

  it("handles a failed load instead of leaving the rejection unhandled", async () => {
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    getNotifications.mockRejectedValue(new Error("network down"));

    render(<Notifications />);

    await waitFor(() => expect(getNotifications).toHaveBeenCalled());
    expect(await screen.findByText(/all caught up/i)).toBeTruthy();
    await waitFor(() => expect(onError).toHaveBeenCalled());
    onError.mockRestore();
  });
});

describe("Notifications — mark all as read", () => {
  it("sends the request through the shared client", async () => {
    getNotifications.mockResolvedValue({ data: TWO_UNREAD });
    render(<Notifications />);
    await screen.findByText("Quiz graded");

    fireEvent.click(screen.getByText("Mark all as read"));

    await waitFor(() => expect(markAllNotificationsRead).toHaveBeenCalledTimes(1));
  });

  it("clears the unread badge on success", async () => {
    getNotifications.mockResolvedValue({ data: TWO_UNREAD });
    render(<Notifications />);
    expect(await screen.findByText("2 new")).toBeTruthy();

    fireEvent.click(screen.getByText("Mark all as read"));

    await waitFor(() => expect(screen.queryByText("2 new")).toBeNull());
  });

  it("does NOT mark anything read when the request fails", async () => {
    // The false-success bug this migration closes. With `fetch`, a 500 still
    // resolved, the .then ran, and the badge cleared — the UI reported a
    // success the server never performed. axios rejects, so the count must
    // survive.
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    getNotifications.mockResolvedValue({ data: TWO_UNREAD });
    markAllNotificationsRead.mockRejectedValue(new Error("500"));

    render(<Notifications />);
    expect(await screen.findByText("2 new")).toBeTruthy();

    fireEvent.click(screen.getByText("Mark all as read"));

    await waitFor(() => expect(markAllNotificationsRead).toHaveBeenCalled());
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(screen.getByText("2 new")).toBeTruthy();
    onError.mockRestore();
  });
});
