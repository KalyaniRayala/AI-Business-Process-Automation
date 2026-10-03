import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import {
  THREAD,
  workflowList,
  workflowResponse,
  workflowSummary,
} from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

const OTHER = "99999999-0000-0000-0000-000000000000";

function queueRoutes(runs = [workflowSummary()], extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(
      () => ({ body: workflowList(runs, { limit: 50 }) }),
      runs.length,
    ),
    ...extra,
  });
}

describe("approval queue", () => {
  test("lists the workflows that are waiting", async () => {
    const api = queueRoutes([
      workflowSummary(),
      workflowSummary({ thread_id: OTHER, employee_id: "E101", expense_amount: 6000 }),
    ]);

    renderApp("/approvals");

    expect(await screen.findByText("$750")).toBeInTheDocument();
    expect(screen.getByText("$6,000")).toBeInTheDocument();
    expect(screen.getByText("2 waiting")).toBeInTheDocument();

    const call = api.to("GET", "/workflows").find((c) => c.query.get("limit") === "50");

    expect(call?.query.get("status")).toBe("awaiting_approval");
  });

  test("is empty when nothing is waiting", async () => {
    queueRoutes([]);

    renderApp("/approvals");

    expect(await screen.findByText("Nothing is waiting for approval.")).toBeInTheDocument();
  });

  test("the navigation shows how many are waiting", async () => {
    queueRoutes([workflowSummary(), workflowSummary({ thread_id: OTHER })]);

    renderApp("/approvals");

    expect(await screen.findByLabelText("2 waiting")).toHaveTextContent("2");
  });

  test("says when only the newest part of a long queue is shown", async () => {
    mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(() => ({
        body: workflowList([workflowSummary()], { total: 80, limit: 50 }),
      })),
    });

    renderApp("/approvals");

    expect(await screen.findByText("Showing the newest 1 of 80.")).toBeInTheDocument();
  });

  test("reports a failure to load the queue", async () => {
    mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(() => ({ status: 500, body: { detail: "Database is down." } })),
    });

    renderApp("/approvals");

    expect(await screen.findByRole("alert")).toHaveTextContent("Database is down.");
  });
});

describe("reviewing and deciding", () => {
  test("the request is only loaded when the item is opened", async () => {
    const api = queueRoutes([workflowSummary()], {
      [`GET /workflows/${THREAD}`]: { body: workflowResponse() },
    });
    const user = userEvent.setup();

    renderApp("/approvals");

    await screen.findByText("$750");

    expect(api.to("GET", `/workflows/${THREAD}`)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Review" }));

    const panel = await screen.findByRole("article", { name: `Approval request ${THREAD}` });

    expect(within(panel).getByText("Meera (E102), Finance")).toBeInTheDocument();
    expect(within(panel).getByText("Priya (Manager)")).toBeInTheDocument();
    expect(api.to("GET", `/workflows/${THREAD}`)).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Hide" }));

    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  test("a decision updates the queue and confirms what happened", async () => {
    let decided = false;

    const api = mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(
        () => ({ body: workflowList(decided ? [] : [workflowSummary()], { limit: 50 }) }),
        1,
      ),
      [`GET /workflows/${THREAD}`]: { body: workflowResponse() },
      [`POST /workflows/${THREAD}/decision`]: () => {
        decided = true;

        return {
          body: workflowResponse({ status: "success", approval_status: "approved", approval_request: null }),
        };
      },
    });
    const user = userEvent.setup();

    renderApp("/approvals");

    await user.click(await screen.findByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    const notice = await screen.findByText(/Decision recorded/);

    expect(notice.closest("[role=status]")).toHaveTextContent("is now Success");
    expect(within(notice.closest("[role=status]") as HTMLElement).getByRole("link")).toHaveAttribute(
      "href",
      `/workflows/${THREAD}`,
    );

    // The queue is reloaded and the decided item is gone.
    expect(await screen.findByText("Nothing is waiting for approval.")).toBeInTheDocument();
    expect(api.to("POST", `/workflows/${THREAD}/decision`)[0]?.body).toEqual({ decision: "approve" });
  });

  test("a workflow that is no longer waiting says so instead of offering a decision", async () => {
    queueRoutes([workflowSummary()], {
      [`GET /workflows/${THREAD}`]: {
        body: workflowResponse({ status: "success", approval_request: null }),
      },
    });
    const user = userEvent.setup();

    renderApp("/approvals");

    await user.click(await screen.findByRole("button", { name: "Review" }));

    expect(await screen.findByText(/no longer waiting/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  test("a decision that was already made is explained and the form stays usable", async () => {
    queueRoutes([workflowSummary()], {
      [`GET /workflows/${THREAD}`]: { body: workflowResponse() },
      [`POST /workflows/${THREAD}/decision`]: {
        status: 409,
        body: { detail: "A decision was already recorded for this workflow." },
      },
    });
    const user = userEvent.setup();

    renderApp("/approvals");

    await user.click(await screen.findByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Confirm reject" }));

    expect(await screen.findByText("A decision was already recorded for this workflow.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  test("an item can fail to load without breaking the queue", async () => {
    queueRoutes([workflowSummary()], {
      [`GET /workflows/${THREAD}`]: { status: 404, body: { detail: "Workflow not found." } },
    });
    const user = userEvent.setup();

    renderApp("/approvals");

    await user.click(await screen.findByRole("button", { name: "Review" }));

    expect(await screen.findByText("Workflow not found.")).toBeInTheDocument();
    expect(screen.getByText("$750")).toBeInTheDocument();
  });
});
