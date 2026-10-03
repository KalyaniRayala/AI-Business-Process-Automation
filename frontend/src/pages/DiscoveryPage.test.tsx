import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { discovery, THREAD } from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

function routes(body = discovery(), extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(),
    "GET /discovery/workflows": { body },
    ...extra,
  });
}

describe("discovery report", () => {
  test("summarises the window and outcomes", async () => {
    routes();

    renderApp("/discovery");

    expect(await screen.findByText(/10 workflow\(s\) analysed/)).toBeInTheDocument();

    const outcomes = screen.getByRole("region", { name: "Outcomes" });

    expect(within(outcomes).getByText("Completed").closest(".stat")).toHaveTextContent("6");
    expect(within(outcomes).getByText("Awaiting approval").closest(".stat")).toHaveTextContent("2");
  });

  test("lists the paths with share, outcomes and an example", async () => {
    routes();

    renderApp("/discovery");

    const paths = await screen.findByRole("region", { name: "Paths taken" });
    const rows = within(paths).getAllByRole("row").slice(1);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("employee+policy+rag -> approval -> action -> final");
    expect(rows[0]).toHaveTextContent("60%");
    expect(rows[0]).toHaveTextContent("Completed 6");
    expect(rows[0]).toHaveTextContent("2.5 s");
    expect(within(rows[0]!).getByRole("link", { name: "Example" })).toHaveAttribute(
      "href",
      `/workflows/${THREAD}`,
    );

    // A path with no timing or example.
    expect(rows[1]).toHaveTextContent("Failed 1");
    expect(within(rows[1]!).queryByRole("link")).not.toBeInTheDocument();
  });

  test("flags nodes that fail often", async () => {
    routes();

    renderApp("/discovery");

    const steps = await screen.findByRole("region", { name: "Steps" });
    const rows = within(steps).getAllByRole("row").slice(1);

    expect(rows[0]).toHaveTextContent("employee");
    expect(rows[0]).toHaveTextContent("20.0%");
    expect(rows[0]).toHaveTextContent("#5");
    expect(rows[1]).toHaveTextContent("final");
    expect(rows[1]).toHaveTextContent("Not in the process model");
  });

  test("shows approval statistics", async () => {
    routes();

    renderApp("/discovery");

    const approvals = await screen.findByRole("region", { name: "Approvals" });

    expect(within(approvals).getByText("Rejected").closest(".stat")).toHaveTextContent("1");
    expect(within(approvals).getByText("Median wait").closest(".stat")).toHaveTextContent("2m 0s");
    expect(within(approvals).getByText("Still waiting").closest(".stat")).toHaveTextContent("1");
  });

  test("breaks volume down by amount and department", async () => {
    routes();

    renderApp("/discovery");

    const volume = await screen.findByRole("region", { name: "Volume" });

    expect(within(volume).getByText("above 500 up to 5000")).toBeInTheDocument();
    expect(within(volume).getByText("Finance")).toBeInTheDocument();
    expect(within(volume).getByText("$4,500")).toBeInTheDocument();
  });

  test("compares the process model with the automation", async () => {
    routes();

    renderApp("/discovery");

    const model = await screen.findByRole("region", { name: "Process model" });

    expect(within(model).getByText(/3 steps, 0 not fully automated/)).toBeInTheDocument();
    expect(within(model).getByText(/Nodes outside the model: final, recovery/)).toBeInTheDocument();

    const rows = within(model).getAllByRole("row").slice(1);

    expect(rows[0]).toHaveTextContent("Validate receipt");
    expect(rows[0]).toHaveTextContent("Manual");
    expect(rows[0]).toHaveTextContent("Nothing");
    expect(rows[1]).toHaveTextContent("Automated");
    expect(rows[1]).toHaveTextContent("Policy evaluation node");
    expect(rows[2]).toHaveTextContent("Partial");
    expect(rows[2]).toHaveTextContent("Human approval node");
  });

  test("explains an empty window", async () => {
    routes(
      discovery({
        window: { ...discovery().window, workflows_analyzed: 0, first_started_at: null, last_started_at: null },
        variants: [],
        steps: [],
        volume: { workflows_with_run_record: 0, workflows_without_run_record: 0, by_department: [], by_amount_band: [] },
        process_model: null,
      }),
    );

    renderApp("/discovery");

    expect(await screen.findByText("No runs in this window.")).toBeInTheDocument();
    expect(screen.getByText("No step data in this window.")).toBeInTheDocument();
    expect(screen.getByText("No run records to break down.")).toBeInTheDocument();
    expect(screen.getByText("The process is not in the context graph.")).toBeInTheDocument();
  });

  test("reports a failure and can retry", async () => {
    let attempts = 0;

    routes(discovery(), {
      "GET /discovery/workflows": () => {
        attempts += 1;

        return attempts === 1 ? { status: 500, body: { detail: "Analysis failed." } } : { body: discovery() };
      },
    });
    const user = userEvent.setup();

    renderApp("/discovery");

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("Analysis failed.");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByText(/10 workflow\(s\) analysed/)).toBeInTheDocument();
  });
});

describe("analysis window", () => {
  test("sends the limit and range when applied", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/discovery");

    await screen.findByText(/10 workflow\(s\) analysed/);

    await user.type(screen.getByLabelText("Most recent runs"), "250");
    await user.type(screen.getByLabelText("Started after"), "2026-10-01T08:00");
    await user.type(screen.getByLabelText("Started before"), "2026-10-02T08:00");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await screen.findByText(/10 workflow\(s\) analysed/);

    const last = api.to("GET", "/discovery/workflows").at(-1)!;

    expect(last.query.get("limit")).toBe("250");
    expect(last.query.get("since")).toMatch(/^2026-10-01T\d{2}:\d{2}:00\.000Z$/);
    expect(last.query.get("until")).toMatch(/^2026-10-02T\d{2}:\d{2}:00\.000Z$/);
  });

  test("the first request uses the defaults", async () => {
    const api = routes();

    renderApp("/discovery");

    await screen.findByText(/10 workflow\(s\) analysed/);

    const first = api.to("GET", "/discovery/workflows")[0]!;

    expect([...first.query.keys()]).toEqual([]);
  });

  test("an end before the start cannot be applied", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/discovery");

    await screen.findByText(/10 workflow\(s\) analysed/);

    await user.type(screen.getByLabelText("Started after"), "2026-10-02T08:00");
    await user.type(screen.getByLabelText("Started before"), "2026-10-01T08:00");

    expect(screen.getByRole("alert")).toHaveTextContent("The end of the range is before its start.");
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(api.to("GET", "/discovery/workflows")).toHaveLength(1);
  });

  test("a limit above what the API allows is blocked by the form", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/discovery");

    await screen.findByText(/10 workflow\(s\) analysed/);

    const limit = screen.getByLabelText("Most recent runs");

    await user.type(limit, "99999");

    expect(limit).toBeInvalid();

    await user.click(screen.getByRole("button", { name: "Apply" }));

    // The browser's own validation stops the submit: no new request.
    expect(api.to("GET", "/discovery/workflows")).toHaveLength(1);

    await user.clear(limit);
    await user.type(limit, "5000");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await screen.findByText(/10 workflow\(s\) analysed/);

    expect(api.to("GET", "/discovery/workflows").at(-1)?.query.get("limit")).toBe("5000");
  });
});
