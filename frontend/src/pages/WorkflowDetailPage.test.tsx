import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import {
  auditEvent,
  auditPage,
  THREAD,
  workflowResponse,
} from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

const PATH = `/workflows/${THREAD}`;

const completed = workflowResponse({
  status: "success",
  approval_status: "approved",
  approval_request: null,
  answer: "The $750 expense for Meera needs manager approval, which was granted.",
  action_result: {
    success: true,
    reimbursement: {
      transaction_id: "EXP-E102-20261001-ABCDEF",
      employee_id: "E102",
      amount: 750,
      status: "approved",
    },
  },
});

const history = {
  ...auditPage([
    auditEvent({ id: 1, event_type: "workflow_started", message: "Workflow started." }),
    auditEvent({
      id: 2,
      event_type: "node_completed",
      node: "policy",
      status: "success",
      message: "Node 'policy' completed.",
      details: { expense_amount: 750 },
    }),
    auditEvent({
      id: 3,
      event_type: "approval_approved",
      status: "success",
      actor_type: "human",
      actor_id: "Priya",
      message: "Approval granted.",
    }),
  ]),
  thread_id: THREAD,
};

function routes(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(),
    [`GET ${PATH}`]: { body: completed },
    [`GET ${PATH}/audit`]: { body: history },
    ...extra,
  });
}

describe("a finished workflow", () => {
  test("shows the outcome, the answer and the reimbursement", async () => {
    routes();

    renderApp(PATH);

    expect(
      await screen.findByText("The $750 expense for Meera needs manager approval, which was granted."),
    ).toBeInTheDocument();

    const summary = screen.getByRole("region", { name: "Summary" });

    expect(within(summary).getByText("Success")).toBeInTheDocument();
    expect(within(summary).getByText("Approved")).toBeInTheDocument();
    expect(within(summary).getByText("EXP-E102-20261001-ABCDEF")).toBeInTheDocument();
    expect(within(summary).getByText("$750")).toBeInTheDocument();

    // No approval form for a decided workflow.
    expect(screen.queryByRole("heading", { name: "Approval needed" })).not.toBeInTheDocument();
  });

  test("shows the history with who did what", async () => {
    routes();

    renderApp(PATH);

    const region = await screen.findByRole("region", { name: "History" });
    const timeline = await within(region).findAllByRole("listitem");

    expect(timeline).toHaveLength(3);
    expect(timeline[0]).toHaveTextContent("Workflow started");
    expect(timeline[1]).toHaveTextContent("Node completed");
    expect(timeline[1]).toHaveTextContent("policy");
    expect(timeline[2]).toHaveTextContent("By human: Priya");

    // Raw details are available but collapsed.
    expect(within(timeline[1]!).getByText("Details")).toBeInTheDocument();
    expect(within(timeline[1]!).getByText(/"expense_amount": 750/)).toBeInTheDocument();
  });

  test("shows the question when opened from the list", async () => {
    routes();

    renderApp({ pathname: PATH, state: { question: "For employee E102, process a $750 expense." } });

    expect(await screen.findByText("For employee E102, process a $750 expense.")).toBeInTheDocument();
  });

  test("shows an error and a message for a failed workflow", async () => {
    routes({
      [`GET ${PATH}`]: {
        body: workflowResponse({
          status: "failed",
          approval_request: null,
          error_message: "Employee E999 was not found.",
          answer: "I was unable to complete the request.",
        }),
      },
    });

    renderApp(PATH);

    const summary = await screen.findByRole("region", { name: "Summary" });

    expect(await within(summary).findByRole("alert")).toHaveTextContent("Employee E999 was not found.");
    expect(within(summary).getByText("Failed")).toBeInTheDocument();
    expect(within(summary).getByText("I was unable to complete the request.")).toBeInTheDocument();
  });
});

describe("loading problems", () => {
  test("an unknown workflow is reported", async () => {
    routes({
      [`GET ${PATH}`]: { status: 404, body: { detail: `Workflow ${THREAD} was not found.` } },
      [`GET ${PATH}/audit`]: { status: 404, body: { detail: `Workflow ${THREAD} was not found.` } },
    });

    renderApp(PATH);

    const alerts = await screen.findAllByRole("alert");

    expect(alerts[0]).toHaveTextContent("Workflow not found");
    expect(alerts[0]).toHaveTextContent(`Workflow ${THREAD} was not found.`);
  });

  test("the history can fail without hiding the workflow, and be retried", async () => {
    let attempts = 0;

    routes({
      [`GET ${PATH}/audit`]: () => {
        attempts += 1;

        return attempts === 1 ? { status: 500, body: { detail: "Audit unavailable." } } : { body: history };
      },
    });
    const user = userEvent.setup();

    renderApp(PATH);

    // The summary still renders.
    expect(await screen.findByText(/which was granted/)).toBeInTheDocument();

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("Audit unavailable.");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Workflow started.")).toBeInTheDocument();
  });

  test("an empty history is explained", async () => {
    routes({ [`GET ${PATH}/audit`]: { body: { ...auditPage([]), thread_id: THREAD } } });

    renderApp(PATH);

    expect(await screen.findByText("No audit events recorded for this workflow.")).toBeInTheDocument();
  });
});

describe("a workflow waiting for approval", () => {
  test("shows the approval form and updates after a decision", async () => {
    const api = routes({
      [`GET ${PATH}`]: { body: workflowResponse() },
      [`POST ${PATH}/decision`]: { body: completed },
    });
    const user = userEvent.setup();

    renderApp(PATH);

    expect(await screen.findByRole("heading", { name: "Approval needed" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Your name (optional)"), "Priya");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    // The page switches to the result without leaving.
    expect(await screen.findByText(/which was granted/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Approval needed" })).not.toBeInTheDocument();
    expect(screen.getByText("EXP-E102-20261001-ABCDEF")).toBeInTheDocument();

    expect(api.to("POST", `${PATH}/decision`)[0]?.body).toEqual({
      decision: "approve",
      approver: "Priya",
    });

    // The history is reloaded to include the decision.
    expect(api.to("GET", `${PATH}/audit`).length).toBeGreaterThanOrEqual(2);
  });

  test("a rejected decision shows the rejection", async () => {
    routes({
      [`GET ${PATH}`]: { body: workflowResponse() },
      [`POST ${PATH}/decision`]: {
        body: workflowResponse({
          status: "rejected",
          approval_status: "rejected",
          approval_request: null,
          answer: "The expense approval request for Meera was rejected.",
        }),
      },
    });
    const user = userEvent.setup();

    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Confirm reject" }));

    expect(await screen.findByText(/was rejected/)).toBeInTheDocument();

    const summary = screen.getByRole("region", { name: "Summary" });

    expect(within(summary).getAllByText("Rejected").length).toBeGreaterThan(0);
  });

  test("a waiting workflow without a request does not show the form", async () => {
    routes({ [`GET ${PATH}`]: { body: workflowResponse({ approval_request: null }) } });

    renderApp(PATH);

    await screen.findByRole("region", { name: "Summary" });

    expect(screen.queryByRole("heading", { name: "Approval needed" })).not.toBeInTheDocument();
  });
});
