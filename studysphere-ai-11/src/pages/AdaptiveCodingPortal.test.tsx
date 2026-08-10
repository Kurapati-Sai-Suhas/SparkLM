/**
 * Adaptive Coding Portal — Run Code parity (M1/P1.2-A).
 *
 * The legacy `/code` portal was the only surface with a Run button, and it
 * shared ONE output string and ONE executing flag with Submit — so a run could
 * leave text on screen that read like a verdict. This suite pins the ported
 * behaviour and, more importantly, the separation: Run and Submit answer
 * different questions and must never write to each other's state.
 *
 * Monaco is replaced with a textarea. The real editor renders to canvas and
 * cannot be typed into by Testing Library, and "Run sends the CURRENT editor
 * contents" is exactly the property that must be provable.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * Dispatch N clicks inside ONE act() so React batches them.
 *
 * `fireEvent` flushes state between calls, so by the second click React has
 * re-rendered and marked the button disabled — and React consults `disabled`
 * from its own fiber props, not the DOM, so no amount of DOM manipulation
 * reaches the handler. Batching the dispatches delivers the second one while
 * the props still say enabled, which is the only way to exercise the
 * production concurrency guard rather than the UI layer in front of it.
 */
async function burstClick(node: Element, times: number) {
  await act(async () => {
    for (let i = 0; i < times; i++) {
      node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
  });
}

/** A promise whose settlement the test controls explicitly — no timers. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: any) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// Recharts' ResponsiveContainer constructs a ResizeObserver on mount and jsdom
// ships none. Polyfilled here rather than in the shared setup so this phase
// changes no configuration other tests depend on.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as any).ResizeObserver ??= ResizeObserverStub;

const runCode = vi.fn();

vi.mock("@/services/api", () => ({
  getAccessToken: () => "test-token",
  codeAPI: { runCode: (...a: any[]) => runCode(...a) },
}));

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: any) => (
    <textarea
      aria-label="editor"
      value={value}
      onChange={(e: any) => onChange(e.target.value)}
    />
  ),
}));

// Children that fetch on mount — out of scope here, and their requests would
// otherwise land in the global fetch mock and confuse the assertions.
vi.mock("../components/LearningPathVisualizer", () => ({ default: () => null }));
vi.mock("../components/ReviewQueueCard", () => ({ default: () => null }));
vi.mock("../components/ProblemDescription", () => ({
  default: ({ content }: any) => <div>{content}</div>,
}));

import AdaptiveCodingPortal from "./AdaptiveCodingPortal";
import { MemoryRouter } from "react-router-dom";

const PROBLEM = {
  id: "999",
  title: "[Array] Echo",
  difficulty: "Easy",
  description: "Return the input.",
  explanation: "Matched to your skill level.",
  boilerplate_code: { python: "class Solution:\n    def solve(self):\n        pass" },
  sample_case: { stdin: "1 2 3" },
  advanced_xai: {
    xai: {
      dominant_factor: "Topic Recency",
      success_probability: 42,
      shap_values: [{ subject: "Logic Accuracy", A: 50, fullMark: 100 }],
      recommendation: "Practice arrays.",
    },
    decay_info: { decay_percent: 12 },
  },
};

const OK_RUN = {
  status: "Accepted",
  status_id: 3,
  stdout: "6",
  stderr: "",
  compile_output: "",
  time: "0.05",
  memory: 25000,
};

function mockNextProblem(problem: any = PROBLEM) {
  (global.fetch as any) = vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/api/code/next/")) {
      return Promise.resolve({ json: () => Promise.resolve(problem) });
    }
    // submit
    return Promise.resolve({
      json: () =>
        Promise.resolve({
          status: "accepted",
          all_passed: true,
          passed: 3,
          total: 3,
          test_results: [],
        }),
    });
  });
}

async function renderPortal(problem: any = PROBLEM) {
  mockNextProblem(problem);
  render(
    <MemoryRouter>
      <AdaptiveCodingPortal />
    </MemoryRouter>
  );
  await screen.findByTestId("coding-portal");
}

beforeEach(() => {
  vi.clearAllMocks();
  runCode.mockResolvedValue({ data: OK_RUN });
});

afterEach(() => cleanup());

describe("Run Code — request contract", () => {
  it("renders a Run control distinct from Submit", async () => {
    await renderPortal();
    expect(screen.getByTestId("run-code-btn")).toBeTruthy();
    expect(screen.getByTestId("submit-code-btn")).toBeTruthy();
    expect(screen.getByTestId("run-code-btn").textContent).toMatch(/run/i);
    expect(screen.getByTestId("submit-code-btn").textContent).toMatch(/submit/i);
  });

  it("goes through the shared API client, never raw fetch", async () => {
    await renderPortal();
    const fetchCallsBefore = (global.fetch as any).mock.calls.length;

    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() => expect(runCode).toHaveBeenCalledTimes(1));
    // The only fetch calls are the page's own /code/next/ — Run added none.
    expect((global.fetch as any).mock.calls.length).toBe(fetchCallsBefore);
  });

  it("sends the CURRENT editor contents, not the boilerplate it loaded with", async () => {
    await renderPortal();
    fireEvent.change(screen.getByLabelText("editor"), {
      target: { value: "print(sum(map(int, input().split())))" },
    });

    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() => expect(runCode).toHaveBeenCalled());
    expect(runCode.mock.calls[0][0].code).toBe("print(sum(map(int, input().split())))");
  });

  it("sends problem_id — without it the backend runs bare source that prints nothing", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() => expect(runCode).toHaveBeenCalled());
    expect(runCode.mock.calls[0][0].problemId).toBe("999");
  });

  it("sends the question's public sample stdin", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() => expect(runCode).toHaveBeenCalled());
    expect(runCode.mock.calls[0][0].stdin).toBe("1 2 3");
  });

  it("sends the selected language", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() => expect(runCode).toHaveBeenCalled());
    expect(runCode.mock.calls[0][0].language).toBe("python");
  });
});

describe("Run Code — output rendering", () => {
  it("shows stdout", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    expect((await screen.findByTestId("run-stdout")).textContent).toContain("6");
  });

  it("shows a compile error instead of pretending it ran", async () => {
    runCode.mockResolvedValue({
      data: { ...OK_RUN, stdout: "", status: "Compilation Error", compile_output: "SyntaxError: bad" },
    });
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    expect((await screen.findByTestId("run-compile-error")).textContent).toContain("SyntaxError");
    expect(screen.queryByTestId("run-stdout")).toBeNull();
  });

  it("shows a runtime error instead of pretending it ran", async () => {
    runCode.mockResolvedValue({
      data: { ...OK_RUN, stdout: "", status: "Runtime Error", stderr: "IndexError" },
    });
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    expect((await screen.findByTestId("run-stderr")).textContent).toContain("IndexError");
    expect(screen.queryByTestId("run-stdout")).toBeNull();
  });

  it("reports a transport failure rather than a silent success", async () => {
    runCode.mockRejectedValue({ response: { data: { error: "Judge0 timed out." } } });
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    expect((await screen.findByTestId("run-client-error")).textContent).toContain("Judge0 timed out.");
  });

  it("states plainly when the question ships no sample input", async () => {
    // Must not fabricate stdin or imply the run was meaningful.
    await renderPortal({ ...PROBLEM, sample_case: null });
    fireEvent.click(screen.getByTestId("run-code-btn"));

    expect(await screen.findByTestId("run-no-sample")).toBeTruthy();
    expect(runCode.mock.calls[0][0].stdin).toBe("");
  });
});

describe("Run and Submit are separate states", () => {
  it("labels the console so a run cannot be read as a verdict", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("console-mode").textContent).toMatch(/run result/i)
    );
    expect(screen.getByTestId("console-mode").textContent).not.toMatch(/submission result/i);
  });

  it("a Run never produces a submission result block", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("run-code-btn"));

    await screen.findByTestId("run-stdout");
    expect(screen.queryByTestId("results-block")).toBeNull();
  });

  it("a Submit never produces a run block", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("submit-code-btn"));

    await screen.findByTestId("results-block");
    expect(screen.queryByTestId("run-stdout")).toBeNull();
    expect(screen.getByTestId("console-mode").textContent).toMatch(/submission result/i);
  });

  it("a Run leaves submission state genuinely untouched", async () => {
    // Run writing into `results` is invisible while the console is in run
    // mode, so asserting "no results block after a Run" cannot catch it —
    // mutation testing proved that. A FAILED submit is where it surfaces:
    // `results` stays null on failure, so the console must fall back to
    // "awaiting submission". If a Run had populated `results`, the submission
    // block would render instead, showing run data dressed as a verdict.
    await renderPortal();

    fireEvent.click(screen.getByTestId("run-code-btn"));
    await screen.findByTestId("run-stdout");

    (global.fetch as any) = vi.fn().mockRejectedValue(new Error("network down"));
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    fireEvent.click(screen.getByTestId("submit-code-btn"));

    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(screen.queryByTestId("results-block")).toBeNull();
    expect(screen.getByText(/awaiting submission/i)).toBeTruthy();
    onError.mockRestore();
  });

  it("Submit leaves run STATE untouched, not merely hidden", async () => {
    // The invariant is about state, not display: after a Submit, runResult must
    // still hold the Run response. Observed by starting a new Run and holding
    // it pending — the console then shows run mode with the PREVIOUS result
    // still present. If Submit had nulled runResult, there would be nothing to
    // show. "No run block after a Submit" cannot catch this, which is why the
    // earlier version of this test could not kill the mutation.
    await renderPortal();

    fireEvent.click(screen.getByTestId("run-code-btn"));
    expect((await screen.findByTestId("run-stdout")).textContent).toContain("6");

    fireEvent.click(screen.getByTestId("submit-code-btn"));
    await screen.findByTestId("results-block");

    const pending = deferred<any>();
    runCode.mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTestId("run-code-btn"));

    // Still "6": Submit did not write to runResult.
    expect((await screen.findByTestId("run-stdout")).textContent).toContain("6");
    await act(async () => { pending.resolve({ data: OK_RUN }); });
  });

  it("Run leaves submission STATE untouched across the reverse order", async () => {
    await renderPortal();

    fireEvent.click(screen.getByTestId("submit-code-btn"));
    const before = (await screen.findByTestId("results-block")).textContent;

    fireEvent.click(screen.getByTestId("run-code-btn"));
    await screen.findByTestId("run-stdout");

    fireEvent.click(screen.getByTestId("submit-code-btn"));
    expect((await screen.findByTestId("results-block")).textContent).toBe(before);
  });

  it("keeps the submission result after a later Run, and vice versa", async () => {
    await renderPortal();

    fireEvent.click(screen.getByTestId("submit-code-btn"));
    await screen.findByTestId("results-block");

    fireEvent.click(screen.getByTestId("run-code-btn"));
    await screen.findByTestId("run-stdout");

    // Switching back must show the submission again — Run did not destroy it.
    fireEvent.click(screen.getByTestId("submit-code-btn"));
    expect(await screen.findByTestId("results-block")).toBeTruthy();
  });
});

describe("Run Code — concurrency", () => {
  it("disables both actions while a run is in flight", async () => {
    let release: (v: any) => void = () => {};
    runCode.mockReturnValue(new Promise((r) => { release = r; }));
    await renderPortal();

    fireEvent.click(screen.getByTestId("run-code-btn"));

    await waitFor(() =>
      expect((screen.getByTestId("run-code-btn") as HTMLButtonElement).disabled).toBe(true)
    );
    expect((screen.getByTestId("submit-code-btn") as HTMLButtonElement).disabled).toBe(true);

    release({ data: OK_RUN });
  });

  it("a double click issues exactly one execution", async () => {
    let release: (v: any) => void = () => {};
    runCode.mockReturnValue(new Promise((r) => { release = r; }));
    await renderPortal();

    const btn = screen.getByTestId("run-code-btn");
    fireEvent.click(btn);
    fireEvent.click(btn);
    fireEvent.click(btn);

    await waitFor(() => expect(runCode).toHaveBeenCalledTimes(1));
    release({ data: OK_RUN });
  });

  it("the in-flight guard itself blocks a second execution, and then resets", async () => {
    // Exercises the PRODUCTION guard, not the disabled attribute in front of
    // it. Both clicks are delivered before React can re-render, so the handler
    // runs twice and only the ref stops the second request.
    const first = deferred<any>();
    runCode.mockReturnValueOnce(first.promise);
    await renderPortal();
    const btn = screen.getByTestId("run-code-btn");

    await burstClick(btn, 3);
    expect(runCode).toHaveBeenCalledTimes(1);

    // Guard releases on settle…
    runCode.mockResolvedValue({ data: { ...OK_RUN, stdout: "second" } });
    await act(async () => { first.resolve({ data: OK_RUN }); });

    // …so a later run is allowed through.
    fireEvent.click(screen.getByTestId("run-code-btn"));
    await waitFor(() => expect(runCode).toHaveBeenCalledTimes(2));
    expect((await screen.findByTestId("run-stdout")).textContent).toContain("second");
  });

  it("a Run and a Submit dispatched together start only ONE execution", async () => {
    // Regression for a race this review found. Run and Submit originally kept
    // separate guards — Run a ref, Submit the `submitting`/`running` state.
    // Dispatched in the same batch, Run set its ref synchronously while
    // Submit's closure still read a stale `running === false`, so BOTH fired
    // and two Judge0 executions ran at once against a 10/min budget.
    // Measured before the fix: [runCode 1, submit 1]. One shared gate now.
    const pending = deferred<any>();
    runCode.mockReturnValue(pending.promise);
    await renderPortal();
    const fetchBefore = (global.fetch as any).mock.calls.length;

    await act(async () => {
      screen.getByTestId("run-code-btn").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      screen.getByTestId("submit-code-btn").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const submitCalls = (global.fetch as any).mock.calls
      .slice(fetchBefore)
      .filter((c: any[]) => String(c[0]).includes("/api/code/submit/"));

    expect(runCode).toHaveBeenCalledTimes(1);
    expect(submitCalls).toHaveLength(0);

    await act(async () => { pending.resolve({ data: OK_RUN }); });
  });

  it("the gate releases after a Submit, so a later Run is admitted", async () => {
    await renderPortal();

    fireEvent.click(screen.getByTestId("submit-code-btn"));
    await screen.findByTestId("results-block");

    fireEvent.click(screen.getByTestId("run-code-btn"));
    await waitFor(() => expect(runCode).toHaveBeenCalledTimes(1));
  });

  it("a later Run replaces the earlier result", async () => {
    // NOT a stale-response test. Runs are single-flight, so two cannot be in
    // flight together and an out-of-order overwrite is unconstructible — the
    // monotonic request id that used to guard it was removed as provably dead.
    // What remains worth asserting is that a second run supersedes the first.
    let releaseFirst: (v: any) => void = () => {};
    runCode.mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
    await renderPortal();

    fireEvent.click(screen.getByTestId("run-code-btn"));
    await waitFor(() => expect(runCode).toHaveBeenCalledTimes(1));

    // Second run resolves immediately with the NEWER answer.
    runCode.mockResolvedValue({ data: { ...OK_RUN, stdout: "NEWER" } });
    // Force the in-flight guard open by settling the first request first.
    releaseFirst({ data: { ...OK_RUN, stdout: "STALE" } });
    await screen.findByTestId("run-stdout");

    fireEvent.click(screen.getByTestId("run-code-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("run-stdout").textContent).toContain("NEWER")
    );
  });
});

describe("Existing portal behaviour is untouched", () => {
  it("still renders the XAI panel and its radar data", async () => {
    await renderPortal();
    expect(screen.getByTestId("xai-radar-container")).toBeTruthy();
    expect(screen.getByText(/42/)).toBeTruthy();
  });

  it("still submits through the existing flow", async () => {
    await renderPortal();
    fireEvent.click(screen.getByTestId("submit-code-btn"));

    await screen.findByTestId("results-block");
    const submitCall = (global.fetch as any).mock.calls.find((c: any[]) =>
      String(c[0]).includes("/api/code/submit/")
    );
    expect(submitCall).toBeTruthy();
    expect(JSON.parse(submitCall[1].body).problem_id).toBe("999");
  });
});
