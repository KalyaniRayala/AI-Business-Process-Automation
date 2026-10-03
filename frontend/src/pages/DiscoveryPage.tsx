import { CircleCheck, CircleX, Clock, ShieldCheck, Timer } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, type WindowParams } from "../api/client";
import type { Discovery, DurationSummary } from "../api/types";
import { WindowFilter } from "../components/WindowFilter";
import {
  AutomationBadge,
  Badge,
  Bar,
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
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

function Duration({ summary }: { summary: DurationSummary | null }) {
  if (!summary) return <span className="muted">-</span>;

  return (
    <span title={`mean ${formatDuration(summary.mean)}, p95 ${formatDuration(summary.p95)}, max ${formatDuration(summary.max)}`}>
      {formatDuration(summary.median)}
    </span>
  );
}

function Variants({ data }: { data: Discovery }) {
  if (data.variants.length === 0) return <EmptyState>No runs in this window.</EmptyState>;

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Paths workflows took</caption>
        <thead>
          <tr>
            <th>Path</th>
            <th className="numeric">Runs</th>
            <th>Share</th>
            <th>Outcomes</th>
            <th>Median cycle time</th>
            <th>
              <span className="visually-hidden">Example</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {data.variants.map((variant) => (
            <tr key={variant.variant_id}>
              <td>
                <code className="path">{variant.path_text}</code>
              </td>
              <td className="numeric">{variant.workflows}</td>
              <td className="share">
                <Bar value={variant.share} label={`${variant.path_text} share`} />
                <span>{formatPercent(variant.share)}</span>
              </td>
              <td>
                <span className="chips">
                  {Object.entries(variant.outcomes).map(([name, count]) => (
                    <span key={name}>
                      <StatusBadge status={name} /> {count}
                    </span>
                  ))}
                </span>
              </td>
              <td>
                <Duration summary={variant.cycle_time_seconds} />
              </td>
              <td>
                {variant.sample_thread_ids[0] && (
                  <Link to={`/workflows/${variant.sample_thread_ids[0]}`}>Example</Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Steps({ data }: { data: Discovery }) {
  if (data.steps.length === 0) return <EmptyState>No step data in this window.</EmptyState>;

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Workflow node statistics</caption>
        <thead>
          <tr>
            <th>Node</th>
            <th className="numeric">Runs</th>
            <th className="numeric">Failures</th>
            <th className="numeric">Failure rate</th>
            <th>Median duration</th>
            <th>Process step</th>
          </tr>
        </thead>
        <tbody>
          {data.steps.map((step) => (
            <tr key={step.node}>
              <td>{step.node}</td>
              <td className="numeric">{step.executions}</td>
              <td className="numeric">{step.failures}</td>
              <td className="numeric">
                {step.failure_rate > 0 ? (
                  <Badge tone={step.failure_rate >= 0.05 ? "danger" : "warning"}>
                    {formatPercent(step.failure_rate, 1)}
                  </Badge>
                ) : (
                  formatPercent(0)
                )}
              </td>
              <td>
                <Duration summary={step.duration_seconds} />
              </td>
              <td>
                {step.modeled_steps.length > 0 ? (
                  step.modeled_steps.map((n) => `#${n}`).join(", ")
                ) : (
                  <span className="muted">Not in the process model</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Approvals({ data }: { data: Discovery }) {
  const a = data.approvals;

  return (
    <dl className="stats">
      <Stat label="Needed approval" icon={ShieldCheck} value={a.required_approval} hint={formatPercent(a.approval_rate_of_workflows)} />
      <Stat label="Approved" icon={CircleCheck} value={a.approved} tone="success" />
      <Stat label="Rejected" icon={CircleX} value={a.rejected} hint={formatPercent(a.rejection_rate)} />
      <Stat label="Still waiting" icon={Clock} value={a.pending} tone={a.pending > 0 ? "warning" : undefined} />
      <Stat
        label="Median wait" icon={Timer}
        value={formatDuration(a.wait_seconds?.median)}
        hint={a.wait_seconds ? `p95 ${formatDuration(a.wait_seconds.p95)}` : "No decisions yet"}
      />
    </dl>
  );
}

function Volume({ data }: { data: Discovery }) {
  const { volume } = data;

  if (volume.workflows_with_run_record === 0) {
    return <EmptyState>No run records to break down.</EmptyState>;
  }

  return (
    <div className="two-column">
      <div className="table-wrap">
        <table>
          <caption>By amount</caption>
          <thead>
            <tr>
              <th>Band</th>
              <th className="numeric">Runs</th>
              <th className="numeric">Needed approval</th>
              <th className="numeric">Rejected</th>
            </tr>
          </thead>
          <tbody>
            {volume.by_amount_band.map((band) => (
              <tr key={band.band}>
                <td>{band.band}</td>
                <td className="numeric">{band.workflows}</td>
                <td className="numeric">{band.required_approval}</td>
                <td className="numeric">{band.rejected}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="table-wrap">
        <table>
          <caption>By department</caption>
          <thead>
            <tr>
              <th>Department</th>
              <th className="numeric">Runs</th>
              <th className="numeric">Total amount</th>
              <th className="numeric">Rejected</th>
            </tr>
          </thead>
          <tbody>
            {volume.by_department.map((row) => (
              <tr key={row.department}>
                <td>{row.department}</td>
                <td className="numeric">{row.workflows}</td>
                <td className="numeric">{formatCurrency(row.total_amount)}</td>
                <td className="numeric">{row.rejected}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProcessModel({ data }: { data: Discovery }) {
  const model = data.process_model;

  if (!model) {
    return <EmptyState>The process is not in the context graph.</EmptyState>;
  }

  return (
    <>
      <p className="muted">
        {model.label}: {model.steps.length} steps, {model.automation_gaps.length} not fully
        automated.
        {model.unmapped_nodes.length > 0 &&
          ` Nodes outside the model: ${model.unmapped_nodes.join(", ")}.`}
      </p>

      <div className="table-wrap">
        <table>
          <caption className="visually-hidden">Process steps and their automation</caption>
          <thead>
            <tr>
              <th className="numeric">#</th>
              <th>Step</th>
              <th>Performed by</th>
              <th>Automation</th>
              <th>Implemented by</th>
              <th className="numeric">Runs observed</th>
            </tr>
          </thead>
          <tbody>
            {model.steps.map((step) => (
              <tr key={step.step}>
                <td className="numeric">{step.order ?? "-"}</td>
                <td>
                  {step.label}
                  {step.uses_systems.length > 0 && (
                    <small className="muted"> ({step.uses_systems.join(", ")})</small>
                  )}
                </td>
                <td>{step.performed_by.join(", ") || <span className="muted">Unspecified</span>}</td>
                <td>
                  <AutomationBadge level={step.automation} />
                </td>
                <td>
                  {step.implemented_by.length > 0
                    ? step.implemented_by.map((c) => c.label).join(", ")
                    : <span className="muted">Nothing</span>}
                </td>
                <td className="numeric">{step.workflows_observed ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function DiscoveryPage() {
  const [range, setRange] = useState<WindowParams>({});

  const discovery = useAsync((signal) => api.discovery(range, signal), [range]);
  const data = discovery.data;

  return (
    <>
      <PageHeader
        title="Workflow discovery"
        subtitle="How the process actually runs, learned from the audit trail and compared with the process model."
      />

      <WindowFilter onApply={setRange} />

      {discovery.error && <ErrorMessage error={discovery.error} onRetry={discovery.reload} />}
      {!data && discovery.loading && <Loading />}

      {data && (
        <>
          <p className="muted" aria-live="polite">
            {data.window.workflows_analyzed} workflow(s) analysed
            {data.window.first_started_at &&
              `, started ${formatDateTime(data.window.first_started_at)} to ${formatDateTime(data.window.last_started_at)}`}
            .
          </p>

          <Section title="Outcomes">
            <dl className="stats">
              {Object.entries(data.outcomes).map(([name, count]) => (
                <Stat key={name} label={humanize(name)} value={count} />
              ))}
            </dl>
          </Section>

          <Section title="Paths taken">
            <Variants data={data} />
          </Section>

          <Section title="Steps">
            <Steps data={data} />
          </Section>

          <Section title="Approvals">
            <Approvals data={data} />
          </Section>

          <Section title="Volume">
            <Volume data={data} />
          </Section>

          <Section title="Process model">
            <ProcessModel data={data} />
          </Section>
        </>
      )}
    </>
  );
}
