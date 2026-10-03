import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { WorkflowResponse, WorkflowSummary } from "../api/types";
import { ApprovalPanel } from "../components/ApprovalPanel";
import {
  Badge,
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
  Section,
  StatusBadge,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatCurrency, formatDateTime } from "../utils/format";

const QUEUE_LIMIT = 50;

function ApprovalItem({
  run,
  onDecided,
}: {
  run: WorkflowSummary;
  onDecided: (result: WorkflowResponse) => void;
}) {
  const [open, setOpen] = useState(false);

  // Loaded only when the item is opened.
  const detail = useAsync(
    (signal) => (open ? api.getWorkflow(run.thread_id, signal) : Promise.resolve(undefined)),
    [open, run.thread_id],
  );

  return (
    <li className="queue__item">
      <div className="queue__head">
        <div>
          <strong>{formatCurrency(run.expense_amount)}</strong>{" "}
          <span className="muted">for {run.employee_id ?? "unknown employee"}</span>
          <p className="truncate" title={run.question}>
            {run.question}
          </p>
          <small className="muted">
            Waiting since <time dateTime={run.created_at}>{formatDateTime(run.created_at)}</time>
          </small>
        </div>

        <div className="button-row">
          <Link to={`/workflows/${run.thread_id}`} state={{ question: run.question }}>
            History
          </Link>
          <button
            type="button"
            className="button"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Hide" : "Review"}
          </button>
        </div>
      </div>

      {open && (
        <div className="queue__body">
          {detail.error && <ErrorMessage error={detail.error} onRetry={detail.reload} />}
          {!detail.data && detail.loading && <Loading />}

          {detail.data && detail.data.status !== "awaiting_approval" && (
            <p className="muted">
              This workflow is no longer waiting: <StatusBadge status={detail.data.status} />
            </p>
          )}

          {detail.data?.status === "awaiting_approval" && detail.data.approval_request && (
            <ApprovalPanel
              threadId={run.thread_id}
              request={detail.data.approval_request}
              onDecided={onDecided}
            />
          )}
        </div>
      )}
    </li>
  );
}

export function ApprovalsPage() {
  const queue = useAsync(
    (signal) => api.listWorkflows({ status: "awaiting_approval", limit: QUEUE_LIMIT }, signal),
    [],
  );

  const [lastDecision, setLastDecision] = useState<WorkflowResponse | null>(null);

  function onDecided(result: WorkflowResponse) {
    setLastDecision(result);
    queue.reload();
  }

  return (
    <>
      <PageHeader
        title="Approvals"
        subtitle="Expense workflows paused until a person approves or rejects them."
      />

      {lastDecision && (
        <div className="alert alert--success" role="status">
          Decision recorded: workflow{" "}
          <Link to={`/workflows/${lastDecision.thread_id}`}>
            {lastDecision.thread_id.slice(0, 8)}
          </Link>{" "}
          is now <StatusBadge status={lastDecision.status} />.
        </div>
      )}

      <Section
        title="Waiting for a decision"
        actions={
          queue.data ? (
            <Badge tone={queue.data.total > 0 ? "warning" : "neutral"}>
              {queue.data.total} waiting
            </Badge>
          ) : undefined
        }
      >
        {queue.error && <ErrorMessage error={queue.error} onRetry={queue.reload} />}
        {!queue.data && queue.loading && <Loading />}

        {queue.data &&
          (queue.data.workflows.length === 0 ? (
            <EmptyState>Nothing is waiting for approval.</EmptyState>
          ) : (
            <>
              <ul className="queue">
                {queue.data.workflows.map((run) => (
                  <ApprovalItem key={run.thread_id} run={run} onDecided={onDecided} />
                ))}
              </ul>

              {queue.data.total > queue.data.workflows.length && (
                <p className="muted">
                  Showing the newest {queue.data.workflows.length} of {queue.data.total}.
                </p>
              )}
            </>
          ))}
      </Section>
    </>
  );
}
