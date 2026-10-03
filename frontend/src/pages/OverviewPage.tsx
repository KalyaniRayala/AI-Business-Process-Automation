import { Activity, CircleCheck, CircleX, Clock, ShieldCheck, Timer } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import {
  Bar,
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
  PriorityBadge,
  Section,
  Stat,
  StatusBadge,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  formatPercent,
  humanize,
} from "../utils/format";

const OUTCOME_ORDER = [
  "completed",
  "rejected",
  "failed",
  "error",
  "awaiting_approval",
  "in_progress",
] as const;

const OUTCOME_COLORS: Record<string, string> = {
  completed: "var(--success)",
  rejected: "var(--danger)",
  failed: "var(--danger-strong)",
  error: "var(--danger-strong)",
  awaiting_approval: "var(--warning)",
  in_progress: "var(--info)",
};

function OutcomeBar({ outcomes }: { outcomes: Record<string, number> }) {
  const total = Object.values(outcomes).reduce((sum, n) => sum + n, 0);

  if (total === 0) return <EmptyState>No workflow runs to summarise yet.</EmptyState>;

  return (
    <>
      <div className="stacked-bar" role="img" aria-label="Workflow outcomes">
        {OUTCOME_ORDER.map((name) => {
          const count = outcomes[name] ?? 0;

          return count > 0 ? (
            <span
              key={name}
              style={{ width: `${(count / total) * 100}%`, background: OUTCOME_COLORS[name] }}
              title={`${humanize(name)}: ${count}`}
            />
          ) : null;
        })}
      </div>

      <ul className="legend">
        {OUTCOME_ORDER.map((name) => (
          <li key={name}>
            <span className="legend__swatch" style={{ background: OUTCOME_COLORS[name] }} />
            {humanize(name)}: <strong>{outcomes[name] ?? 0}</strong>
          </li>
        ))}
      </ul>
    </>
  );
}

export function OverviewPage() {
  const discovery = useAsync((signal) => api.discovery({}, signal), []);
  const recommendations = useAsync((signal) => api.recommendations({}, signal), []);
  const recent = useAsync((signal) => api.listWorkflows({ limit: 5 }, signal), []);
  const pending = useAsync(
    (signal) => api.listWorkflows({ status: "awaiting_approval", limit: 1 }, signal),
    [],
  );

  const data = discovery.data;
  const failed = data ? (data.outcomes["failed"] ?? 0) + (data.outcomes["error"] ?? 0) : 0;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="How the expense process is running, and what to improve next."
      />

      {discovery.error && <ErrorMessage error={discovery.error} onRetry={discovery.reload} />}
      {!data && discovery.loading && <Loading />}

      {data && (
        <Section title="Process health">
          <dl className="stats">
            <Stat
              label="Workflows analysed" icon={Activity}
              value={data.window.workflows_analyzed}
              hint={
                data.window.first_started_at
                  ? `Since ${formatDateTime(data.window.first_started_at)}`
                  : "No runs yet"
              }
            />
            <Stat
              label="Completed" icon={CircleCheck}
              value={data.outcomes["completed"] ?? 0}
              tone="success"
              hint={formatPercent(
                data.window.workflows_analyzed
                  ? (data.outcomes["completed"] ?? 0) / data.window.workflows_analyzed
                  : null,
              )}
            />
            <Stat
              label="Waiting for approval" icon={Clock}
              value={pending.data?.total ?? data.outcomes["awaiting_approval"] ?? 0}
              tone="warning"
              hint={<Link to="/approvals">Review queue</Link>}
            />
            <Stat label="Failed" icon={CircleX} value={failed} tone={failed > 0 ? "danger" : undefined} />
            <Stat
              label="Needed approval" icon={ShieldCheck}
              value={formatPercent(data.approvals.approval_rate_of_workflows)}
              hint={`${data.approvals.rejected} rejected`}
            />
            <Stat
              label="Average approval wait" icon={Timer}
              value={formatDuration(data.approvals.wait_seconds?.mean)}
              hint={
                data.approvals.wait_seconds
                  ? `p95 ${formatDuration(data.approvals.wait_seconds.p95)}`
                  : "No decisions yet"
              }
            />
          </dl>

          <OutcomeBar outcomes={data.outcomes} />
        </Section>
      )}

      <Section
        title="Top recommendations"
        actions={<Link to="/recommendations">See all</Link>}
      >
        {recommendations.error && (
          <ErrorMessage error={recommendations.error} onRetry={recommendations.reload} />
        )}
        {!recommendations.data && recommendations.loading && <Loading />}

        {recommendations.data &&
          (recommendations.data.recommendations.length === 0 ? (
            <EmptyState>No recommendations right now.</EmptyState>
          ) : (
            <ol className="reco-list">
              {recommendations.data.recommendations.slice(0, 3).map((item) => (
                <li key={item.recommendation_id}>
                  <div className="reco-list__head">
                    <strong>{item.title}</strong>
                    <PriorityBadge priority={item.priority} />
                  </div>
                  <p>{item.summary}</p>
                  <Bar value={item.score / 100} label={`Score ${item.score}`} />
                  <small className="muted">Score {item.score}</small>
                </li>
              ))}
            </ol>
          ))}
      </Section>

      <Section title="Recent workflows" actions={<Link to="/workflows">All workflows</Link>}>
        {recent.error && <ErrorMessage error={recent.error} onRetry={recent.reload} />}
        {!recent.data && recent.loading && <Loading />}

        {recent.data &&
          (recent.data.workflows.length === 0 ? (
            <EmptyState>No workflows yet.</EmptyState>
          ) : (
            <ul className="plain-list">
              {recent.data.workflows.map((run) => (
                <li key={run.thread_id}>
                  <Link to={`/workflows/${run.thread_id}`} state={{ question: run.question }}>
                    {run.employee_id ?? "Unknown employee"} · {formatCurrency(run.expense_amount)}
                  </Link>{" "}
                  <StatusBadge status={run.status} />{" "}
                  <small className="muted">{formatDateTime(run.created_at)}</small>
                </li>
              ))}
            </ul>
          ))}
      </Section>
    </>
  );
}
