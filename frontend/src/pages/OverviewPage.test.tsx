import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import {
  discovery,
  recommendation,
  recommendations,
  THREAD,
  workflowList,
  workflowSummary,
} from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

function routes(extra: Parameters<typeof mockApi>[0] = {}, pending = 2) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(
      () => ({ body: workflowList([workflowSummary()]) }),
      pending,
    ),
    "GET /discovery/workflows": { body: discovery() },
    "GET /automation/recommendations": {
      body: recommendations([
        recommendation(),
        recommendation({
          recommendation_id: "reduce_step_failures:employee",
          title: "Reduce failures in the employee step",
          summary: "The employee step fails 20% of the time (2/10).",
          priority: "medium",
          score: 55,
        }),
      ]),
    },
    ...extra,
  });
}

describe("overview", () => {
  test("shows the key numbers", async () => {
    routes();

    renderApp("/");

    const health = await screen.findByRole("region", { name: "Process health" });

    const stat = (label: string) => within(health).getByText(label).closest(".stat") as HTMLElement;

    expect(stat("Workflows analysed")).toHaveTextContent("10");
    expect(stat("Completed")).toHaveTextContent("6");
    expect(stat("Completed")).toHaveTextContent("60%");
    // The waiting count comes from the live queue, not the analysis window.
    expect(stat("Waiting for approval")).toHaveTextContent("2");
    expect(stat("Failed")).toHaveTextContent("1");
    expect(stat("Needed approval")).toHaveTextContent("40%");
    expect(stat("Needed approval")).toHaveTextContent("1 rejected");
    expect(stat("Average approval wait")).toHaveTextContent("2m 0s");
    expect(stat("Average approval wait")).toHaveTextContent("p95 4m 0s");
  });

  test("shows the outcome breakdown", async () => {
    routes();

    renderApp("/");

    const bar = await screen.findByRole("img", { name: "Workflow outcomes" });

    expect(bar.querySelectorAll("span")).toHaveLength(4);   // outcomes with a non-zero count

    expect(screen.getByText(/Completed:/).closest("li")).toHaveTextContent("Completed: 6");
    expect(screen.getByText(/In progress:/).closest("li")).toHaveTextContent("In progress: 0");
  });

  test("shows the top recommendations with their priority and score", async () => {
    routes();

    renderApp("/");

    const section = await screen.findByRole("region", { name: "Top recommendations" });
    const items = await within(section).findAllByRole("listitem");

    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Automate: Notify employee of the final decision");
    expect(items[0]).toHaveTextContent("High priority");
    expect(items[0]).toHaveTextContent("Score 84.5");
    expect(items[1]).toHaveTextContent("Medium priority");

    expect(within(section).getByRole("link", { name: "See all" })).toHaveAttribute(
      "href",
      "/recommendations",
    );
  });

  test("shows only the three best recommendations", async () => {
    routes({
      "GET /automation/recommendations": {
        body: recommendations(
          [1, 2, 3, 4, 5].map((n) =>
            recommendation({ recommendation_id: `r${n}`, title: `Recommendation ${n}` }),
          ),
        ),
      },
    });

    renderApp("/");

    const section = await screen.findByRole("region", { name: "Top recommendations" });

    expect(await within(section).findAllByRole("listitem")).toHaveLength(3);
    expect(within(section).queryByText("Recommendation 4")).not.toBeInTheDocument();
  });

  test("links to recent workflows", async () => {
    routes();

    renderApp("/");

    const section = await screen.findByRole("region", { name: "Recent workflows" });
    const link = await within(section).findByRole("link", { name: /E102 · \$750/ });

    expect(link).toHaveAttribute("href", `/workflows/${THREAD}`);
    expect(within(section).getByText("Awaiting approval")).toBeInTheDocument();
  });

  test("navigates to the approvals queue", async () => {
    routes({
      [`GET /workflows/${THREAD}`]: { body: {} },
    });
    const user = userEvent.setup();

    renderApp("/");

    await user.click(await screen.findByRole("link", { name: "Review queue" }));

    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
  });
});

describe("overview without data or with failures", () => {
  test("handles a system with no runs", async () => {
    routes({
      "GET /discovery/workflows": {
        body: discovery({
          window: { ...discovery().window, workflows_analyzed: 0, first_started_at: null, last_started_at: null },
          outcomes: { completed: 0, rejected: 0, failed: 0, error: 0, awaiting_approval: 0, in_progress: 0 },
          approvals: { ...discovery().approvals, required_approval: 0, approval_rate_of_workflows: 0, rejected: 0, wait_seconds: null },
        }),
      },
      "GET /automation/recommendations": { body: recommendations([]) },
      "GET /workflows": workflowsRoute(() => ({ body: workflowList([]) }), 0),
    });

    renderApp("/");

    expect(await screen.findByText("No workflow runs to summarise yet.")).toBeInTheDocument();
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(screen.getByText("No decisions yet")).toBeInTheDocument();
    expect(screen.getByText("No recommendations right now.")).toBeInTheDocument();
    expect(screen.getByText("No workflows yet.")).toBeInTheDocument();
  });

  test("one failing section does not hide the others", async () => {
    routes({
      "GET /discovery/workflows": { status: 500, body: { detail: "Discovery failed." } },
    });

    renderApp("/");

    expect(await screen.findByRole("alert")).toHaveTextContent("Discovery failed.");

    // Recommendations and recent workflows still render.
    expect(await screen.findByText("Automate: Notify employee of the final decision")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /E102 · \$750/ })).toBeInTheDocument();
  });

  test("an unreachable backend is called out", async () => {
    routes({
      "GET /discovery/workflows": () => {
        throw new TypeError("Failed to fetch");
      },
    });

    renderApp("/");

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(/Backend unreachable|Something went wrong/);
  });
});
