import { useState, type FormEvent } from "react";

import { ApiError, api } from "../api/client";
import type { ApprovalRequest, WorkflowResponse } from "../api/types";
import { formatCurrency } from "../utils/format";
import { BusinessContextView } from "./BusinessContextView";
import { Badge } from "./ui";

type Decision = "approve" | "reject";

const MAX_APPROVER = 100;
const MAX_COMMENT = 2000;

/**
 * A paused workflow's approval request, with the decision form.
 *
 * Deciding is final and approving runs the business action, so choosing a
 * decision asks for confirmation first. The approver name is free text: the
 * backend has no user authentication yet.
 */
export function ApprovalPanel({
  threadId,
  request,
  onDecided,
}: {
  threadId: string;
  request: ApprovalRequest;
  onDecided: (result: WorkflowResponse) => void;
}) {
  const [approver, setApprover] = useState("");
  const [comment, setComment] = useState("");
  const [choice, setChoice] = useState<Decision | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const employee = request.employee?.employee;
  const amount = request.policy?.expense_amount;

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!choice || submitting) return;

    setSubmitting(true);
    setError(null);

    try {
      const result = await api.submitDecision(threadId, {
        decision: choice,
        approver: approver.trim() || undefined,
        comment: comment.trim() || undefined,
      });

      onDecided(result);
    } catch (failure) {
      setError(
        failure instanceof ApiError || failure instanceof Error
          ? failure.message
          : "The decision could not be recorded.",
      );
      setChoice(null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <article className="approval" aria-label={`Approval request ${threadId}`}>
      <p>{request.message}</p>

      <dl className="facts">
        <div>
          <dt>Employee</dt>
          <dd>
            {employee
              ? `${employee.name} (${employee.employee_id}), ${employee.department}`
              : "Unknown"}
          </dd>
        </div>
        <div>
          <dt>Expense amount</dt>
          <dd>{formatCurrency(amount)}</dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd>{request.policy?.approval ?? "Not evaluated"}</dd>
        </div>
        {employee && (
          <div>
            <dt>Manager</dt>
            <dd>{employee.manager}</dd>
          </div>
        )}
      </dl>

      {request.business_context && <BusinessContextView summary={request.business_context} />}

      <form onSubmit={submit} className="form">
        <label>
          Your name (optional)
          <input
            type="text"
            value={approver}
            maxLength={MAX_APPROVER}
            onChange={(e) => setApprover(e.target.value)}
            autoComplete="off"
          />
        </label>

        <label>
          Comment (optional)
          <textarea
            value={comment}
            maxLength={MAX_COMMENT}
            rows={2}
            onChange={(e) => setComment(e.target.value)}
          />
        </label>

        <p className="muted">
          <small>
            The name is not verified: the API has no authentication yet. A decision cannot be
            changed once it is recorded.
          </small>
        </p>

        {error && (
          <div className="alert alert--danger" role="alert">
            {error}
          </div>
        )}

        {choice === null ? (
          <div className="button-row">
            <button type="button" className="button button--primary" onClick={() => setChoice("approve")}>
              Approve
            </button>
            <button type="button" className="button button--danger" onClick={() => setChoice("reject")}>
              Reject
            </button>
          </div>
        ) : (
          <div className="confirm" role="group" aria-label="Confirm decision">
            <p>
              <Badge tone={choice === "approve" ? "success" : "danger"}>
                {choice === "approve" ? "Approve" : "Reject"}
              </Badge>{" "}
              {choice === "approve"
                ? `Approving records the reimbursement${amount !== undefined ? ` of ${formatCurrency(amount)}` : ""}.`
                : "Rejecting ends the workflow without a reimbursement."}
            </p>
            <div className="button-row">
              <button
                type="submit"
                className={`button ${choice === "approve" ? "button--primary" : "button--danger"}`}
                disabled={submitting}
              >
                {submitting ? "Saving..." : `Confirm ${choice}`}
              </button>
              <button
                type="button"
                className="button"
                disabled={submitting}
                onClick={() => setChoice(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </form>
    </article>
  );
}
