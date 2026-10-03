import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { approvalRequest, THREAD, workflowResponse } from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { ApprovalPanel } from "./ApprovalPanel";

const DECISION_PATH = `/workflows/${THREAD}/decision`;

function setup(request = approvalRequest()) {
  const onDecided = vi.fn();

  render(<ApprovalPanel threadId={THREAD} request={request} onDecided={onDecided} />);

  return { onDecided, user: userEvent.setup() };
}

describe("what the approver sees", () => {
  test("shows the employee, amount, policy and manager", () => {
    setup();

    expect(screen.getByText("Meera (E102), Finance")).toBeInTheDocument();
    expect(screen.getByText("$750")).toBeInTheDocument();
    expect(screen.getByText("Manager approval required.")).toBeInTheDocument();
    expect(screen.getAllByText("Priya").length).toBeGreaterThan(0);
  });

  test("shows the context graph facts and their document evidence", () => {
    setup();

    expect(screen.getByRole("heading", { name: "Context graph" })).toBeInTheDocument();
    expect(screen.getByText("Priya (Manager)")).toBeInTheDocument();
    expect(
      screen.getByText("Expenses above $500 require manager approval"),
    ).toBeInTheDocument();
    expect(screen.getByText(/document:data\/expense_process.txt#L15/)).toBeInTheDocument();
    expect(screen.getByText("Graph and policy tool agree")).toBeInTheDocument();
  });

  test("warns when the graph and the policy tool disagree", () => {
    const request = approvalRequest();

    request.business_context!.policy_check = {
      consistent: false,
      graph_approvers: ["Finance"],
      policy_approvers: ["Manager"],
    };

    setup(request);

    const alert = screen.getByRole("alert");

    expect(alert).toHaveTextContent("The context graph and the policy tool disagree");
    expect(alert).toHaveTextContent("The graph requires Finance; the policy tool requires Manager");
    expect(alert).toHaveTextContent("The policy tool decides");
  });

  test("works without graph context", () => {
    const request = approvalRequest();

    delete request.business_context;

    setup(request);

    expect(screen.queryByRole("heading", { name: "Context graph" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  test("copes with a missing employee record", () => {
    setup(approvalRequest({ employee: undefined, policy: undefined }));

    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByText("Not evaluated")).toBeInTheDocument();
  });

  test("limits the free-text fields like the API does", () => {
    setup();

    expect(screen.getByLabelText("Your name (optional)")).toHaveAttribute("maxlength", "100");
    expect(screen.getByLabelText("Comment (optional)")).toHaveAttribute("maxlength", "2000");
  });
});

describe("deciding", () => {
  test("approving asks for confirmation, then sends the decision", async () => {
    const api = mockApi({
      [`POST ${DECISION_PATH}`]: { body: workflowResponse({ status: "success", approval_request: null }) },
    });
    const { onDecided, user } = setup();

    await user.type(screen.getByLabelText("Your name (optional)"), "Priya");
    await user.type(screen.getByLabelText("Comment (optional)"), "Looks fine");

    await user.click(screen.getByRole("button", { name: "Approve" }));

    // Nothing is sent until it is confirmed.
    expect(api.calls).toHaveLength(0);

    const confirm = screen.getByRole("group", { name: "Confirm decision" });

    expect(confirm).toHaveTextContent("Approving records the reimbursement of $750.");

    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    await waitFor(() => expect(onDecided).toHaveBeenCalledTimes(1));

    expect(api.calls).toHaveLength(1);
    expect(api.calls[0]).toMatchObject({
      method: "POST",
      path: DECISION_PATH,
      body: { decision: "approve", approver: "Priya", comment: "Looks fine" },
    });
    expect(onDecided.mock.calls[0]![0]).toMatchObject({ status: "success" });
  });

  test("rejecting says no reimbursement is made", async () => {
    const api = mockApi({
      [`POST ${DECISION_PATH}`]: { body: workflowResponse({ status: "rejected", approval_request: null }) },
    });
    const { onDecided, user } = setup();

    await user.click(screen.getByRole("button", { name: "Reject" }));

    expect(screen.getByRole("group", { name: "Confirm decision" })).toHaveTextContent(
      "Rejecting ends the workflow without a reimbursement.",
    );

    await user.click(screen.getByRole("button", { name: "Confirm reject" }));

    await waitFor(() => expect(onDecided).toHaveBeenCalled());

    expect(api.calls[0]?.body).toEqual({ decision: "reject" });
  });

  test("blank name and comment are left out, and text is trimmed", async () => {
    const api = mockApi({ [`POST ${DECISION_PATH}`]: { body: workflowResponse() } });
    const { onDecided, user } = setup();

    await user.type(screen.getByLabelText("Your name (optional)"), "   ");
    await user.type(screen.getByLabelText("Comment (optional)"), "  ok  ");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    await waitFor(() => expect(onDecided).toHaveBeenCalled());

    expect(api.calls[0]?.body).toEqual({ decision: "approve", comment: "ok" });
  });

  test("cancelling sends nothing and returns to the choice", async () => {
    const api = mockApi({});
    const { onDecided, user } = setup();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Confirm decision" })).not.toBeInTheDocument();
    expect(api.calls).toHaveLength(0);
    expect(onDecided).not.toHaveBeenCalled();
  });

  test("a decision that was already made shows the server's message", async () => {
    mockApi({
      [`POST ${DECISION_PATH}`]: {
        status: 409,
        body: { detail: "A decision was already recorded for this workflow." },
      },
    });
    const { onDecided, user } = setup();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A decision was already recorded for this workflow.",
    );

    // Back to the choice so the person can see what happened and retry or leave.
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(onDecided).not.toHaveBeenCalled();
  });

  test("a network failure is reported", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const { user } = setup();

    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Confirm reject" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the API");
  });

  test("the confirm button is disabled while the decision is being saved", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });

    const api = mockApi({
      [`POST ${DECISION_PATH}`]: async () => {
        await gate;
        return { body: workflowResponse() };
      },
    });
    const { onDecided, user } = setup();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    const saving = await screen.findByRole("button", { name: "Saving..." });

    expect(saving).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();

    // A second click while saving does not send a second decision.
    await user.click(saving);
    expect(api.calls).toHaveLength(1);

    release?.();

    await waitFor(() => expect(onDecided).toHaveBeenCalledTimes(1));
  });
});
