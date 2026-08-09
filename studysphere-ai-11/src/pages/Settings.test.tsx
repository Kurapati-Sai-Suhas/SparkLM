/**
 * Settings page — data flow through the shared API client
 * (N3 Phase 1a, Task 4).
 *
 * This page has been broken in production for THREE independent reasons:
 *
 *   1. it fetched the RELATIVE path "/api/settings/profile/", which on
 *      Vercel resolves against the SPA's own origin where the catch-all
 *      rewrite serves index.html with HTTP 200 — so it parsed the app's own
 *      HTML as JSON;
 *   2. it read a localStorage key nothing ever wrote, sending `Bearer null`
 *      (fixed in the M5 Phase 4 follow-up);
 *   3. the backend raised on `profile.bio`, a field that lives on User, not
 *      Profile (fixed in M5 Phase 1).
 *
 * The most interesting defect is on the save path. `fetch` resolves on ANY
 * HTTP status, so the try/catch around it never fired and the page reported
 * "Profile saved" on a 400 or 500. The catch was always correct — it simply
 * could not run. axios rejects on non-2xx, so it now does.
 *
 * Queried by the page's own data-testid attributes, which already existed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const getSettings = vi.fn();
const updateSettings = vi.fn();
const sendTestEmail = vi.fn();

vi.mock("@/services/api", () => ({
  userAPI: {
    getSettings: (...a: any[]) => getSettings(...a),
    updateSettings: (...a: any[]) => updateSettings(...a),
    sendTestEmail: (...a: any[]) => sendTestEmail(...a),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastPlain = vi.fn();
vi.mock("sonner", () => {
  const toast: any = (...a: any[]) => toastPlain(...a);
  toast.success = (...a: any[]) => toastSuccess(...a);
  toast.error = (...a: any[]) => toastError(...a);
  return { toast };
});

import Settings from "./Settings";

const PROFILE = {
  first_name: "Ada",
  last_name: "Lovelace",
  email: "ada@example.com",
  bio: "Analytical engines",
  email_alerts: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  getSettings.mockResolvedValue({ data: PROFILE });
  updateSettings.mockResolvedValue({ data: { status: "success" } });
  sendTestEmail.mockResolvedValue({ data: { status: "Email sent successfully!" } });
});

afterEach(() => cleanup());

/**
 * Radix TabsTrigger activates on mouseDown, not click — a synthetic click
 * leaves the panel unmounted and every query inside it fails. Verified by
 * probing the rendered testids before and after each event.
 */
function openNotificationsTab() {
  fireEvent.mouseDown(screen.getByTestId("tab-notifications"));
}

describe("Settings — loading the profile", () => {
  it("populates the form from the API", async () => {
    render(<Settings />);

    await waitFor(() =>
      expect((screen.getByTestId("profile-first-name") as HTMLInputElement).value).toBe("Ada")
    );
    expect((screen.getByTestId("profile-email") as HTMLInputElement).value).toBe("ada@example.com");
    expect((screen.getByTestId("profile-bio") as HTMLTextAreaElement).value).toBe("Analytical engines");
  });

  it("requests the profile through the shared client, once", async () => {
    render(<Settings />);

    await waitFor(() => expect(getSettings).toHaveBeenCalledTimes(1));
  });

  it("ignores a payload with no email rather than blanking the form", async () => {
    // The guard the original code had. A body without `email` (an error
    // page, a partial response) must not overwrite state.
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    getSettings.mockResolvedValue({ data: { detail: "unexpected" } });

    render(<Settings />);

    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect((screen.getByTestId("profile-email") as HTMLInputElement).value).toBe("");
    // Proves the GUARD skipped it rather than an exception being swallowed.
    expect(onError).not.toHaveBeenCalled();
    onError.mockRestore();
  });

  it("handles a failed load instead of leaving the rejection unhandled", async () => {
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    getSettings.mockRejectedValue(new Error("network down"));

    render(<Settings />);

    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(screen.getByTestId("settings-page")).toBeTruthy();
    onError.mockRestore();
  });
});

describe("Settings — saving the profile", () => {
  it("sends the edited profile through the shared client", async () => {
    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId("profile-first-name"), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByTestId("profile-save-btn"));

    await waitFor(() => expect(updateSettings).toHaveBeenCalledTimes(1));
    expect(updateSettings.mock.calls[0][0].first_name).toBe("Grace");
    expect(updateSettings.mock.calls[0][0].email).toBe("ada@example.com");
  });

  it("confirms only on success", async () => {
    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("profile-save-btn"));

    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Profile saved"));
    expect(toastError).not.toHaveBeenCalled();
  });

  it("reports failure instead of claiming the profile was saved", async () => {
    // THE headline bug. `fetch` resolved on a 400 (duplicate or malformed
    // email) so the try/catch never fired and the page said "Profile saved"
    // when nothing had been. The catch was always correct; it could not run.
    updateSettings.mockRejectedValue({ response: { status: 400 } });

    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("profile-save-btn"));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Failed to save profile"));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe("Settings — email alerts toggle", () => {
  it("updates the switch immediately, before the request resolves", async () => {
    // Optimistic behaviour that existed before and must survive: the toggle
    // flips first, the test email is sent after.
    // PROFILE has email_alerts: true, so start from OFF — only switching ON
    // sends a test email.
    getSettings.mockResolvedValue({ data: { ...PROFILE, email_alerts: false } });
    let resolveSend: (v: any) => void = () => {};
    sendTestEmail.mockReturnValue(new Promise((r) => { resolveSend = r; }));

    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    openNotificationsTab();
    const toggle = screen.getByTestId("switch-email-alerts");
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(toggle);

    // The request is deliberately left PENDING. The switch is controlled by
    // profile.email_alerts, so if it reads "checked" while nothing has
    // resolved, the state was updated optimistically — which is the
    // behaviour that existed before this migration and had to survive it.
    //
    // Asserting the toast/request instead would pass with the optimistic
    // update deleted; mutation testing caught exactly that.
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
    expect(sendTestEmail).toHaveBeenCalledTimes(1);
    expect(toastPlain).toHaveBeenCalled();

    resolveSend({ data: { status: "Email sent successfully!" } });
  });

  it("reports the server's reason when SMTP fails", async () => {
    // TestEmailView returns 500 {status:"error", message:<reason>}. With
    // `fetch` that resolved and the else-branch showed `data.message`. axios
    // rejects, so the same field has to be read off err.response.data or the
    // user loses the reason — and the rejection would be unhandled.
    getSettings.mockResolvedValue({ data: { ...PROFILE, email_alerts: false } });
    sendTestEmail.mockRejectedValue({
      response: { data: { status: "error", message: "SMTP auth failed" } },
    });

    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    openNotificationsTab();
    fireEvent.click(screen.getByTestId("switch-email-alerts"));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("SMTP auth failed"));
  });

  it("reports a 2xx response that is not the success string as a failure", async () => {
    // The `else` inside .then(): reached only if the server answers 2xx with
    // something other than "Email sent successfully!". TestEmailView cannot
    // do that today — it returns either that exact string with 200, or 500,
    // and axios routes 500 to .catch — so this branch is unreachable against
    // the current backend and exists as insurance against it changing.
    //
    // Pinned anyway, because mutation testing flagged it: replacing the
    // toast.error with a toast.success failed nothing. An unreachable branch
    // that is also unverified is indistinguishable from a broken one, and the
    // next person running mutants should not have to re-derive why it lived.
    getSettings.mockResolvedValue({ data: { ...PROFILE, email_alerts: false } });
    sendTestEmail.mockResolvedValue({
      data: { status: "error", message: "SMTP relay refused" },
    });

    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    openNotificationsTab();
    fireEvent.click(screen.getByTestId("switch-email-alerts"));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("SMTP relay refused"));
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("falls back to a generic message when the server gives no reason", async () => {
    getSettings.mockResolvedValue({ data: { ...PROFILE, email_alerts: false } });
    sendTestEmail.mockRejectedValue(new Error("network down"));

    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    openNotificationsTab();
    fireEvent.click(screen.getByTestId("switch-email-alerts"));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Failed to send email. Check SMTP settings.")
    );
  });

  it("does not send a test email when switching alerts OFF", async () => {
    render(<Settings />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());

    openNotificationsTab();
    // PROFILE has email_alerts: true, so the first click turns it off.
    fireEvent.click(screen.getByTestId("switch-email-alerts"));

    expect(sendTestEmail).not.toHaveBeenCalled();
  });
});
