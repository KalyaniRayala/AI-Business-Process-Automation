import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { WorkflowResponse } from "../api/types";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { AuditTimeline } from "../components/AuditTimeline";
import { ErrorMessage, Loading, PageHeader, Section, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatCurrency } from "../utils/format";

function reimbursementOf(result: Record<string, unknown> | null) {
  const reimbursement = result?.["reimbursement"];

  return reimbursement && typeof reimbursement === "object"
    ? (reimbursement as Record<string, unknown>)
    : null;
}

function Outcome({ workflow }: { workflow: WorkflowResponse }) {
  const reimbursement = reimbursementOf(workflow.action_result);

  return (
    <>
      {workflow.error_message && (
        <div className="alert alert--danger" role="alert">
          {workflow.error_message}
        </div>
      )}

      {workflow.answer && <p className="answer">{workflow.answer}</p>}

      {reimbursement && (
        <dl className="facts">
          <div>
            <dt>Transaction</dt>
            <dd>{String(reimbursement["transaction_id"] ?? "-")}</dd>
          </div>
          <div>
            <dt>Employee</dt>
            <dd>{String(reimbursement["employee_id"] ?? "-")}</dd>
          </div>
          <div>
            <dt>Amount</dt>
            <dd>
              {typeof reimbursement["amount"] === "number"
                ? formatCurrency(reimbursement["amount"])
                : "-"}
            </dd>
          </div>
          <div>
            <dt>Reimbursement status</dt>
            <dd>{String(reimbursement["status"] ?? "-")}</dd>
          </div>
        </dl>
      )}
    </>
  );
}

export function WorkflowDetailPage() {
  const { threadId = "" } = useParams();
  const location = useLocation();

  // Only set when arriving from the list or after starting a workflow.
  const question = (location.state as { question?: string } | null)?.question;

  const workflow = useAsync((signal) => api.getWorkflow(threadId, signal), [threadId]);
  const audit = useAsync((signal) => api.workflowAudit(threadId, { limit: 500 }, signal), [
    threadId,
  ]);

  // The decision response replaces the loaded workflow without a refetch.
  const [decided, setDecided] = useState<WorkflowResponse | null>(null);

  const current = decided ?? workflow.data;

  function onDecided(result: WorkflowResponse) {
    setDecided(result);
    audit.reload();
  }

  return (
    <>
      <PageHeader
        title="Workflow"
        subtitle={<code>{threadId}</code>}
        actions={
          <Link to="/workflows" className="button">
            Back to workflows
          </Link>
        }
      />

      {workflow.error && !current && (
        <ErrorMessage error={workflow.error} onRetry={workflow.reload} title="Workflow not found" />
      )}
      {!current && workflow.loading && <Loading />}

      {current && (
        <>
          <Section title="Summary">
            {question && <p className="question">{question}</p>}

            <dl className="facts">
              <div>
                <dt>Status</dt>
                <dd>
                  <StatusBadge status={current.status} />
                </dd>
              </div>
              <div>
                <dt>Approval</dt>
                <dd>
                  <StatusBadge status={current.approval_status} />
                </dd>
              </div>
            </dl>

            <Outcome workflow={current} />
          </Section>

          {current.status === "awaiting_approval" && current.approval_request && (
            <Section title="Approval needed">
              <ApprovalPanel
                threadId={threadId}
                request={current.approval_request}
                onDecided={onDecided}
              />
            </Section>
          )}
        </>
      )}

      <Section title="History">
        {audit.error && <ErrorMessage error={audit.error} onRetry={audit.reload} />}
        {!audit.data && audit.loading && <Loading />}
        {audit.data &&
          (audit.data.events.length === 0 ? (
            <p className="empty">No audit events recorded for this workflow.</p>
          ) : (
            <AuditTimeline events={audit.data.events} />
          ))}
      </Section>
    </>
  );
}
