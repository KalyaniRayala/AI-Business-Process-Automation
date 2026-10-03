import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { recommendation, recommendations } from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

const failures = recommendation({
  recommendation_id: "reduce_step_failures:employee",
  type: "reduce_step_failures",
  title: "Reduce failures in the employee step",
  summary: "The employee step fails 20% of the time (2/10).",
  target: { kind: "node", key: "employee", label: "employee", order: null },
  priority: "medium",
  score: 55.2,
  scores: { impact: 0.5, feasibility: 0.8, confidence: 0.6 },
  effort: "low",
  rationale: ["'employee' failed in 2 of 10 execution(s) (20%)."],
  suggested_actions: ["Validate the employee ID when the claim is submitted."],
  evidence: [],
});

const approvalWait = recommendation({
  recommendation_id: "reduce_approval_wait:approval",
  type: "reduce_approval_wait",
  title: "Reduce time waiting for approval",
  priority: "low",
  score: 40,
  requires_human_decision: true,
  evidence: [],
});

function routes(body = recommendations([recommendation(), failures, approvalWait]), extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(),
    "GET /automation/recommendations": { body },
    ...extra,
  });
}

describe("recommendation list", () => {
  test("shows each recommendation with its score, priority and reasons", async () => {
    routes();

    renderApp("/recommendations");

    const heading = await screen.findByRole("heading", {
      name: "Automate: Notify employee of the final decision",
    });
    const first = heading.closest("li")!;

    expect(within(first).getByText("Automate a manual step")).toBeInTheDocument();
    expect(within(first).getByLabelText("Score 84.5 out of 100")).toBeInTheDocument();
    expect(within(first).getByText("High priority")).toBeInTheDocument();
    expect(within(first).getByText("Low effort")).toBeInTheDocument();
    expect(within(first).getByText("Medium confidence")).toBeInTheDocument();
    expect(within(first).getByText("'Notify employee' is a manual step: no workflow node implements it.")).toBeInTheDocument();
    expect(within(first).getByText("Send the message automatically when the workflow finishes.")).toBeInTheDocument();
    expect(within(first).getByText(/Source: document:data\/expense_process.txt#L21/)).toBeInTheDocument();
  });

  test("breaks the score into impact, feasibility and confidence", async () => {
    routes();

    renderApp("/recommendations");

    const card = (await screen.findByRole("heading", { name: "Reduce failures in the employee step" })).closest("li")!;

    expect(within(card).getByRole("meter", { name: "Impact 0.5" })).toHaveAttribute("aria-valuenow", "50");
    expect(within(card).getByRole("meter", { name: "Feasibility 0.8" })).toHaveAttribute("aria-valuenow", "80");
    expect(within(card).getByRole("meter", { name: "Confidence 0.6" })).toHaveAttribute("aria-valuenow", "60");
    expect(within(card).getByText("0.80")).toBeInTheDocument();
  });

  test("flags recommendations that need a business decision", async () => {
    routes();

    renderApp("/recommendations");

    const wait = (await screen.findByRole("heading", { name: "Reduce time waiting for approval" })).closest("li")!;
    const plain = (await screen.findByRole("heading", { name: "Reduce failures in the employee step" })).closest("li")!;

    expect(within(wait).getByText("Needs a business decision")).toBeInTheDocument();
    expect(within(plain).queryByText("Needs a business decision")).not.toBeInTheDocument();
    // No source line when there is no evidence.
    expect(within(plain).queryByText(/Source:/)).not.toBeInTheDocument();
  });

  test("summarises how many there are at each priority", async () => {
    routes();

    renderApp("/recommendations");

    await screen.findByRole("heading", { name: "Automate: Notify employee of the final decision" });

    // "Recommendations" is also a navigation link, so look at the stat tiles only.
    const tiles = Array.from(document.querySelectorAll<HTMLElement>(".stat"));
    const stat = (label: string) =>
      tiles.find((tile) => tile.querySelector(".stat__label")?.textContent === label)!;

    expect(stat("Recommendations")).toHaveTextContent("3");
    expect(stat("High priority")).toHaveTextContent("1");
    expect(stat("Medium priority")).toHaveTextContent("1");
    expect(stat("Low priority")).toHaveTextContent("1");
    expect(stat("Runs analysed")).toHaveTextContent("10");
  });

  test("shows warnings from the API", async () => {
    routes(
      recommendations([recommendation()], {
        warnings: ["Only 4 run(s) were analysed; confidence is low."],
      }),
    );

    renderApp("/recommendations");

    expect(await screen.findByText("Only 4 run(s) were analysed; confidence is low.")).toHaveAttribute(
      "role",
      "status",
    );
  });

  test("explains how scores are calculated", async () => {
    routes();

    renderApp("/recommendations");

    const rubric = (await screen.findByText("Scoring rubric")).closest("details")!;

    expect(within(rubric).getByText(/score = 100 \* \(0.5 \* impact/)).toBeInTheDocument();
    expect(within(rubric).getByText("Recommendations are advice; nothing is changed or executed.")).toBeInTheDocument();
    expect(within(rubric).getByText(/High priority from 70, medium from 45/)).toBeInTheDocument();
  });

  test("is empty when there is nothing to recommend", async () => {
    routes(recommendations([]));

    renderApp("/recommendations");

    expect(await screen.findByText("No recommendations at this priority.")).toBeInTheDocument();
  });

  test("reports a failure and can retry", async () => {
    let attempts = 0;

    routes(undefined, {
      "GET /automation/recommendations": () => {
        attempts += 1;

        return attempts === 1
          ? { status: 500, body: { detail: "Could not compute recommendations." } }
          : { body: recommendations([recommendation(), failures]) };
      },
    });
    const user = userEvent.setup();

    renderApp("/recommendations");

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("Could not compute recommendations.");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Reduce failures in the employee step" })).toBeInTheDocument();
  });
});

describe("filtering", () => {
  test("asks the API for a minimum priority", async () => {
    const api = routes(undefined, {
      "GET /automation/recommendations": (request) => {
        const min = request.query.get("min_priority");

        return {
          body: min === "high" ? recommendations([recommendation()]) : recommendations([recommendation(), failures]),
        };
      },
    });
    const user = userEvent.setup();

    renderApp("/recommendations");

    await screen.findByRole("heading", { name: "Reduce failures in the employee step" });

    expect(api.to("GET", "/automation/recommendations")[0]?.query.get("min_priority")).toBe("low");

    await user.selectOptions(screen.getByLabelText("Minimum priority"), "high");

    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "Reduce failures in the employee step" }),
      ).not.toBeInTheDocument(),
    );

    expect(api.to("GET", "/automation/recommendations").at(-1)?.query.get("min_priority")).toBe("high");
  });

  test("combines the priority with the analysis window", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/recommendations");

    await screen.findByRole("heading", { name: "Reduce failures in the employee step" });

    await user.selectOptions(screen.getByLabelText("Minimum priority"), "medium");
    await user.type(screen.getByLabelText("Most recent runs"), "100");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await screen.findByRole("heading", { name: "Reduce failures in the employee step" });

    const last = api.to("GET", "/automation/recommendations").at(-1)!;

    expect(last.query.get("min_priority")).toBe("medium");
    expect(last.query.get("limit")).toBe("100");
  });
});
