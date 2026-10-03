import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { observabilitySummary, readiness } from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";
import { formatUptime } from "./SystemPage";

function routes(overrides: Record<string, unknown> = {}) {
  return {
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(),
    "GET /health/ready": { body: readiness() },
    "GET /observability/summary": { body: observabilitySummary() },
    ...overrides,
  };
}

describe("formatUptime", () => {
  test.each([
    [45, "45s"],
    [125, "2m 5s"],
    [3_725, "1h 2m"],
    [90_000, "1d 1h"],
  ])("formats %i seconds as %s", (seconds, expected) => {
    expect(formatUptime(seconds)).toBe(expected);
  });

  test("copes with a nonsense value", () => {
    expect(formatUptime(-1)).toBe("-");
    expect(formatUptime(Number.NaN)).toBe("-");
  });
});

describe("SystemPage", () => {
  test("shows the readiness checks", async () => {
    mockApi(routes());

    renderApp("/system");

    expect(await screen.findByText("Database")).toBeInTheDocument();

    expect(screen.getByText("postgresql reachable")).toBeInTheDocument();
    expect(screen.getByText("42 chunks")).toBeInTheDocument();
    expect(screen.getByText(/up for 1h 2m/)).toBeInTheDocument();
  });

  test("shows the headline numbers", async () => {
    mockApi(routes());

    renderApp("/system");

    expect(await screen.findByText("Requests served")).toBeInTheDocument();

    const stats = screen.getByText("Requests served").closest("dl");

    expect(within(stats as HTMLElement).getByText("120")).toBeInTheDocument();
    expect(within(stats as HTMLElement).getByText("18")).toBeInTheDocument();
  });

  test("lists the workflow nodes with their failures", async () => {
    mockApi(routes());

    renderApp("/system");

    const section = await screen.findByRole("region", { name: "Workflow nodes" });

    expect(within(section).getByText("Rag")).toBeInTheDocument();
    expect(within(section).getByText("Policy")).toBeInTheDocument();
  });

  test("shows recent errors with their message", async () => {
    mockApi(routes());

    renderApp("/system");

    const section = await screen.findByRole("region", { name: "Recent errors" });

    expect(within(section).getByText("workflow.node.rag")).toBeInTheDocument();
    expect(within(section).getByText("ValueError: boom")).toBeInTheDocument();
  });

  test("shows the route table", async () => {
    mockApi(routes());

    renderApp("/system");

    const section = await screen.findByRole("region", { name: "Requests by route" });

    expect(within(section).getByText("/workflows/{thread_id}")).toBeInTheDocument();
    expect(within(section).getByText("404")).toBeInTheDocument();
  });

  test("says so when nothing has been measured yet", async () => {
    mockApi(
      routes({
        "GET /observability/summary": {
          body: observabilitySummary({
            workflows: {
              by_outcome: {},
              average_seconds: {},
              nodes: [],
              awaiting_approval: 0,
            },
            model_calls: { total: 0, by_operation: [], average_seconds: {} },
            errors: { total: 0, by_kind: [], recent: [] },
            slowest_operations: [],
            recent_operations: [],
          }),
        },
      }),
    );

    renderApp("/system");

    expect(
      await screen.findByText(
        "No workflow nodes have run since this process started.",
      ),
    ).toBeInTheDocument();

    expect(screen.getByText("No errors recorded.")).toBeInTheDocument();
    expect(
      screen.getByText("No model calls since this process started."),
    ).toBeInTheDocument();
  });

  test("shows a degraded backend without treating it as an error", async () => {
    const degraded = readiness({
      status: "degraded",
      checks: [
        {
          name: "workflow_state",
          status: "degraded",
          essential: false,
          detail: "InMemorySaver: workflow state is in memory",
          duration_seconds: 0,
        },
      ],
    });

    mockApi(routes({ "GET /health/ready": { status: 503, body: degraded } }));

    renderApp("/system");

    // Once for the overall verdict, once for the check that caused it.
    expect(await screen.findAllByText("degraded")).toHaveLength(2);

    expect(screen.getByText(/workflow state is in memory/)).toBeInTheDocument();
    expect(screen.getByText("(not essential)")).toBeInTheDocument();
  });

  test("reports a backend that cannot be reached", async () => {
    mockApi(
      routes({
        "GET /health/ready": { status: 500, body: { detail: "it is broken" } },
        "GET /observability/summary": { status: 500, body: { detail: "it is broken" } },
      }),
    );

    renderApp("/system");

    expect(await screen.findAllByText("it is broken")).toHaveLength(2);
  });

  test("reloads when refresh is pressed", async () => {
    const user = userEvent.setup();

    const { calls } = mockApi(routes());

    renderApp("/system");

    await screen.findByText("Requests served");

    const before = calls.filter((call) => call.path === "/observability/summary").length;

    await user.click(screen.getByRole("button", { name: "Refresh now" }));

    await waitFor(() =>
      expect(
        calls.filter((call) => call.path === "/observability/summary").length,
      ).toBeGreaterThan(before),
    );
  });

  test("refreshes on a timer", async () => {
    vi.useFakeTimers();

    try {
      const { calls } = mockApi(routes());

      renderApp("/system");

      const summaryCalls = () =>
        calls.filter((call) => call.path === "/observability/summary").length;

      await vi.waitFor(() => expect(summaryCalls()).toBe(1));

      await vi.advanceTimersByTimeAsync(15_000);

      await vi.waitFor(() => expect(summaryCalls()).toBe(2));
    } finally {
      vi.useRealTimers();
    }
  });

  test("stops refreshing when the timer is turned off", async () => {
    const user = userEvent.setup();

    const { calls } = mockApi(routes());

    renderApp("/system");

    await screen.findByText("Requests served");

    await user.click(screen.getByRole("checkbox", { name: /Refresh every 15 seconds/ }));

    expect(screen.getByRole("checkbox", { name: /Refresh every 15 seconds/ })).not.toBeChecked();

    // Turning it off must not itself trigger another load.
    const after = calls.filter((call) => call.path === "/observability/summary").length;

    expect(after).toBe(1);
  });

  test("asks the backend for a bounded number of rows", async () => {
    const { calls } = mockApi(routes());

    renderApp("/system");

    await screen.findByText("Requests served");

    const call = calls.find((item) => item.path === "/observability/summary");

    expect(call?.query.get("limit")).toBe("10");
  });

  test("is reachable from the navigation", async () => {
    const user = userEvent.setup();

    mockApi(routes());

    // Starting on a page whose own calls are already mocked.
    renderApp("/workflows");

    await user.click(screen.getByRole("link", { name: "System" }));

    expect(await screen.findByRole("heading", { name: "System" })).toBeInTheDocument();
  });
});
