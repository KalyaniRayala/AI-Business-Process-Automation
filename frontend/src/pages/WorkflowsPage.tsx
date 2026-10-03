import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { WORKFLOW_STATUSES, type WorkflowSummary } from "../api/types";
import {
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
  Pagination,
  Section,
  StatusBadge,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatCurrency, formatDateTime, humanize, shortId } from "../utils/format";

const PAGE_SIZE = 20;

const EXAMPLE_QUESTION =
  "For employee E102, explain the expense reimbursement process for a $750 expense and state what approval is required.";

function StartWorkflowForm() {
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!question.trim() || submitting) return;

    setSubmitting(true);
    setError(null);

    try {
      const started = await api.startWorkflow(question.trim());

      navigate(`/workflows/${started.thread_id}`, {
        state: { question: question.trim() },
      });
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error(String(failure)));
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="form form--card" aria-label="Start a workflow">
      <label>
        Question
        <textarea
          value={question}
          rows={3}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={EXAMPLE_QUESTION}
        />
      </label>

      <p className="muted">
        <small>
          Mention an employee ID (such as E102) and an amount. Expenses above $500 pause for
          approval.
        </small>
      </p>

      {error && <ErrorMessage error={error} title="The workflow could not be started" />}

      <div className="button-row">
        <button
          type="submit"
          className="button button--primary"
          disabled={submitting || !question.trim()}
        >
          {submitting ? "Starting..." : "Start workflow"}
        </button>
        <button type="button" className="button" onClick={() => setQuestion(EXAMPLE_QUESTION)}>
          Use example
        </button>
      </div>
    </form>
  );
}

function WorkflowRow({ run }: { run: WorkflowSummary }) {
  return (
    <tr>
      <td>
        <time dateTime={run.created_at}>{formatDateTime(run.created_at)}</time>
      </td>
      <td>{run.employee_id ?? "-"}</td>
      <td className="numeric">{formatCurrency(run.expense_amount)}</td>
      <td>
        <StatusBadge status={run.status} />
      </td>
      <td className="truncate" title={run.question}>
        {run.question}
      </td>
      <td>
        <Link to={`/workflows/${run.thread_id}`} state={{ question: run.question }}>
          Open <span className="visually-hidden">workflow {shortId(run.thread_id)}</span>
        </Link>
      </td>
    </tr>
  );
}

export function WorkflowsPage() {
  const [params, setParams] = useSearchParams();

  const status = params.get("status") ?? "";
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const runs = useAsync(
    (signal) =>
      api.listWorkflows({ status: status || undefined, limit: PAGE_SIZE, offset }, signal),
    [status, offset],
  );

  function update(next: { status?: string; offset?: number }) {
    const merged = new URLSearchParams(params);

    if (next.status !== undefined) {
      if (next.status) merged.set("status", next.status);
      else merged.delete("status");
      merged.delete("offset");
    }

    if (next.offset !== undefined) {
      if (next.offset > 0) merged.set("offset", String(next.offset));
      else merged.delete("offset");
    }

    setParams(merged);
  }

  return (
    <>
      <PageHeader
        title="Workflows"
        subtitle="Every expense workflow, newest first. Start a new one or open a run to see its history."
      />

      <Section title="Start a workflow">
        <StartWorkflowForm />
      </Section>

      <Section
        title="Runs"
        actions={
          <label className="inline-field">
            Status
            <select value={status} onChange={(e) => update({ status: e.target.value })}>
              <option value="">All</option>
              {WORKFLOW_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
          </label>
        }
      >
        {runs.error && <ErrorMessage error={runs.error} onRetry={runs.reload} />}
        {!runs.data && runs.loading && <Loading />}

        {runs.data &&
          (runs.data.workflows.length === 0 ? (
            <EmptyState>
              {status
                ? `No workflows with status "${humanize(status)}".`
                : "No workflows yet. Start one above."}
            </EmptyState>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Started</th>
                      <th>Employee</th>
                      <th className="numeric">Amount</th>
                      <th>Status</th>
                      <th>Question</th>
                      <th>
                        <span className="visually-hidden">Open</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.workflows.map((run) => (
                      <WorkflowRow key={run.thread_id} run={run} />
                    ))}
                  </tbody>
                </table>
              </div>

              <Pagination
                offset={runs.data.offset}
                limit={runs.data.limit}
                total={runs.data.total}
                onChange={(next) => update({ offset: next })}
              />
            </>
          ))}
      </Section>
    </>
  );
}
