import { Clock, Gauge, Power, Server, Sparkles, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type {
  ObservabilitySummary,
  OperationRecord,
  Readiness,
  ReadinessCheck,
} from "../api/types";
import { Badge, EmptyState, ErrorMessage, Loading, PageHeader, Section, Stat } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatDateTime, formatDuration, formatPercent, humanize } from "../utils/format";

const REFRESH_INTERVAL_MS = 15_000;

const CHECK_TONES = {
  ok: "success",
  degraded: "warning",
  failed: "danger",
} as const;

function checkTone(status: string) {
  return CHECK_TONES[status as keyof typeof CHECK_TONES] ?? "neutral";
}

/** Seconds since start, as "3d 4h" / "2h 11m" / "45s". */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "-";

  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${Math.floor(seconds % 60)}s`;

  return `${Math.floor(seconds)}s`;
}

function Checks({ readiness }: { readiness: Readiness }) {
  return (
    <ul className="checks">
      {readiness.checks.map((check: ReadinessCheck) => (
        <li key={check.name}>
          <Badge tone={checkTone(check.status)}>{check.status}</Badge>
          <span className="checks__name">{humanize(check.name)}</span>
          <span className="muted">{check.detail}</span>
          {!check.essential && check.status !== "ok" ? (
            <span className="muted"> (not essential)</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Operations({
  records,
  caption,
  empty,
}: {
  records: OperationRecord[];
  caption: string;
  empty: string;
}) {
  if (records.length === 0) return <EmptyState>{empty}</EmptyState>;

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th>Operation</th>
            <th className="numeric">Duration</th>
            <th>Status</th>
            <th>When</th>
            <th>Request</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record, index) => (
            <tr key={`${record.operation}-${record.started_at}-${index}`}>
              <td>
                <code>{record.operation}</code>
                {record.error ? <div className="muted">{record.error}</div> : null}
              </td>
              <td className="numeric">{formatDuration(record.duration_seconds)}</td>
              <td>
                <Badge tone={record.status === "success" ? "success" : "danger"}>
                  {record.status}
                </Badge>
              </td>
              <td>{formatDateTime(record.started_at)}</td>
              <td>
                <code className="muted">{record.request_id ?? "-"}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Nodes({ data }: { data: ObservabilitySummary }) {
  if (data.workflows.nodes.length === 0) {
    return <EmptyState>No workflow nodes have run since this process started.</EmptyState>;
  }

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Workflow nodes</caption>
        <thead>
          <tr>
            <th>Node</th>
            <th className="numeric">Runs</th>
            <th className="numeric">Failed</th>
            <th className="numeric">Average</th>
          </tr>
        </thead>
        <tbody>
          {data.workflows.nodes.map((node) => (
            <tr key={node.node}>
              <td>{humanize(node.node)}</td>
              <td className="numeric">{node.total}</td>
              <td className="numeric">
                {node.failed > 0 ? (
                  <Badge tone="danger">{node.failed}</Badge>
                ) : (
                  <span className="muted">0</span>
                )}
              </td>
              <td className="numeric">{formatDuration(node.average_seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Routes({ data }: { data: ObservabilitySummary }) {
  if (data.requests.by_route.length === 0) {
    return <EmptyState>Nothing served yet.</EmptyState>;
  }

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Requests by route</caption>
        <thead>
          <tr>
            <th>Route</th>
            <th>Method</th>
            <th>Status</th>
            <th className="numeric">Count</th>
          </tr>
        </thead>
        <tbody>
          {data.requests.by_route.map((row) => (
            <tr key={`${row.method}-${row.route}-${row.status}`}>
              <td>
                <code>{row.route}</code>
              </td>
              <td>{row.method}</td>
              <td>
                <Badge tone={row.status.startsWith("2") ? "success" : "warning"}>
                  {row.status}
                </Badge>
              </td>
              <td className="numeric">{row.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModelCalls({ data }: { data: ObservabilitySummary }) {
  const { model_calls: calls } = data;

  if (calls.by_operation.length === 0) {
    return <EmptyState>No model calls since this process started.</EmptyState>;
  }

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Model calls</caption>
        <thead>
          <tr>
            <th>Operation</th>
            <th>Status</th>
            <th className="numeric">Calls</th>
            <th className="numeric">Average</th>
          </tr>
        </thead>
        <tbody>
          {calls.by_operation.map((row) => (
            <tr key={`${row.provider}-${row.operation}-${row.status}`}>
              <td>
                {row.provider} {humanize(row.operation)}
              </td>
              <td>
                <Badge tone={row.status === "success" ? "success" : "danger"}>
                  {row.status}
                </Badge>
              </td>
              <td className="numeric">{row.count}</td>
              <td className="numeric">
                {formatDuration(calls.average_seconds[row.operation] ?? 0)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SystemPage() {
  const [reloadKey, setReloadKey] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const loadReadiness = useCallback((signal: AbortSignal) => api.readiness(signal), []);

  const loadSummary = useCallback(
    (signal: AbortSignal) => api.observability({ limit: 10 }, signal),
    [],
  );

  const readiness = useAsync(loadReadiness, [reloadKey]);
  const summary = useAsync(loadSummary, [reloadKey]);

  useEffect(() => {
    if (!autoRefresh) return undefined;

    const timer = window.setInterval(() => setReloadKey((key) => key + 1), REFRESH_INTERVAL_MS);

    return () => window.clearInterval(timer);
  }, [autoRefresh]);

  const data = summary.data;

  const failureRate =
    data && data.requests.total > 0 ? data.requests.failed / data.requests.total : 0;

  return (
    <>
      <PageHeader
        title="System"
        subtitle="Live health and activity of the backend process serving this dashboard. These counters reset when it restarts; the audit trail is the durable record."
      />

      <div className="filters">
        <label className="inline-field">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Refresh every 15 seconds
        </label>

        <button
          type="button"
          className="button"
          onClick={() => setReloadKey((key) => key + 1)}
        >
          Refresh now
        </button>
      </div>

      <Section title="Readiness">
        {readiness.error ? (
          <ErrorMessage error={readiness.error} onRetry={() => setReloadKey((key) => key + 1)} />
        ) : readiness.data ? (
          <>
            <p>
              <Badge tone={checkTone(readiness.data.status)}>{readiness.data.status}</Badge>{" "}
              <span className="muted">
                up for {formatUptime(readiness.data.uptime_seconds)}
              </span>
            </p>
            <Checks readiness={readiness.data} />
          </>
        ) : (
          <Loading label="Checking the backend..." />
        )}
      </Section>

      {summary.error ? (
        <ErrorMessage error={summary.error} onRetry={() => setReloadKey((key) => key + 1)} />
      ) : !data ? (
        <Loading label="Loading activity..." />
      ) : (
        <>
          <dl className="stats">
            <Stat label="Requests served" icon={Server} value={data.requests.total} />
            <Stat
              label="Failed responses" icon={TriangleAlert}
              value={data.requests.failed}
              hint={data.requests.total > 0 ? formatPercent(failureRate, 1) : undefined}
            />
            <Stat
              label="Average response" icon={Gauge}
              value={formatDuration(data.requests.average_seconds)}
            />
            <Stat label="Model calls" icon={Sparkles} value={data.model_calls.total} />
            <Stat
              label="Awaiting approval" icon={Clock}
              value={data.workflows.awaiting_approval ?? "-"}
              hint="from the database"
            />
            <Stat label="Started" icon={Power} value={formatDateTime(data.started_at)} />
          </dl>

          <Section title="Workflow nodes">
            <Nodes data={data} />
          </Section>

          <Section title="Model calls">
            <ModelCalls data={data} />
          </Section>

          <Section title="Slowest operations">
            <Operations
              records={data.slowest_operations}
              caption="Slowest recent operations"
              empty="Nothing measured yet."
            />
          </Section>

          <Section title="Recent errors">
            <Operations
              records={data.errors.recent}
              caption="Recent errors"
              empty="No errors recorded."
            />
          </Section>

          <Section title="Requests by route">
            <Routes data={data} />
          </Section>
        </>
      )}
    </>
  );
}
