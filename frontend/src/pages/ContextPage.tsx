import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { ProcessContext } from "../api/types";
import {
  Badge,
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
  Section,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatCurrency, humanize } from "../utils/format";

const PROCESS_KEY = "expense_reimbursement";

function ProcessView({ process }: { process: ProcessContext }) {
  const ruleLabels = new Map(process.policy_rules.map((r) => [r.rule.node_key, r.rule.label]));

  return (
    <>
      <div className="table-wrap">
        <table>
          <caption className="visually-hidden">Process steps</caption>
          <thead>
            <tr>
              <th className="numeric">#</th>
              <th>Step</th>
              <th>Performed by</th>
              <th>Systems</th>
              <th>Governed by</th>
              <th>Automated by</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {process.steps.map((step) => (
              <tr key={step.step.node_key}>
                <td className="numeric">{step.order ?? "-"}</td>
                <td>{step.step.label}</td>
                <td>{step.performed_by.map((r) => r.label).join(", ") || "-"}</td>
                <td>{step.uses_systems.map((s) => s.label).join(", ") || "-"}</td>
                <td>
                  {step.governed_by.length > 0
                    ? step.governed_by.map((key) => ruleLabels.get(key) ?? key).join("; ")
                    : "-"}
                </td>
                <td>
                  {step.implemented_by.length > 0 ? (
                    step.implemented_by.map((item) => (
                      <span key={item.component.node_key} className="chips">
                        {item.component.label}
                        {item.properties?.["coverage"] === "partial" && (
                          <Badge tone="warning">Partial</Badge>
                        )}
                      </span>
                    ))
                  ) : (
                    <Badge tone="danger">Manual</Badge>
                  )}
                </td>
                <td>{step.evidence.join(", ") || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Approval rules</h3>

      <ul className="rules">
        {process.policy_rules.map((rule) => (
          <li key={rule.rule.node_key}>
            {rule.rule.label}
            {rule.approvers.map((approver) => (
              <Badge key={approver.role.node_key} tone="warning">
                {approver.role.label}
                {approver.threshold !== null && ` above ${formatCurrency(approver.threshold)}`}
              </Badge>
            ))}
            {rule.evidence.length > 0 && (
              <small className="muted"> Source: {rule.evidence.join(", ")}</small>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

function NodeExplorer() {
  const [draft, setDraft] = useState("employee:E102");
  const [depth, setDepth] = useState(1);
  const [direction, setDirection] = useState("both");
  const [nodeKey, setNodeKey] = useState("employee:E102");

  const result = useAsync(
    (signal) => api.contextNode(nodeKey, { depth, direction }, signal),
    [nodeKey, depth, direction],
  );

  function submit(event: FormEvent) {
    event.preventDefault();

    if (draft.trim()) setNodeKey(draft.trim());
  }

  function open(key: string) {
    setDraft(key);
    setNodeKey(key);
  }

  return (
    <>
      <form className="filters" onSubmit={submit} aria-label="Node explorer">
        <label>
          Node key
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="employee:E102"
          />
        </label>

        <label>
          Hops
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>

        <label>
          Direction
          <select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="both">Both</option>
            <option value="out">Outgoing</option>
            <option value="in">Incoming</option>
          </select>
        </label>

        <button type="submit" className="button button--primary">
          Explore
        </button>
      </form>

      {result.error && <ErrorMessage error={result.error} onRetry={result.reload} title="Node not found" />}
      {!result.data && result.loading && <Loading />}

      {result.data && (
        <>
          <p>
            <strong>{result.data.node.label}</strong>{" "}
            <Badge>{humanize(result.data.node.node_type)}</Badge>{" "}
            <code>{result.data.node.node_key}</code>
            {!result.data.node.is_active && <Badge tone="warning">Inactive</Badge>}
          </p>

          {result.data.neighbors.length === 0 ? (
            <EmptyState>No related nodes.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <caption className="visually-hidden">Related nodes</caption>
                <thead>
                  <tr>
                    <th className="numeric">Hops</th>
                    <th>Node</th>
                    <th>Type</th>
                    <th>Reached by</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.neighbors.map((item) => (
                    <tr key={item.node.node_key}>
                      <td className="numeric">{item.depth}</td>
                      <td>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => open(item.node.node_key)}
                          title={item.node.node_key}
                        >
                          {item.node.label}
                        </button>
                      </td>
                      <td>{humanize(item.node.node_type)}</td>
                      <td>
                        <code>
                          {item.via.from_key} {humanize(item.via.edge_type).toLowerCase()} {item.via.to_key}
                        </code>
                      </td>
                      <td>{humanize(item.node.source_type)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.data.truncated && (
            <p className="muted">The result was truncated at the neighbor limit.</p>
          )}
        </>
      )}
    </>
  );
}

export function ContextPage() {
  const process = useAsync((signal) => api.process(PROCESS_KEY, signal), []);

  return (
    <>
      <PageHeader
        title="Context graph"
        subtitle="The business process, its rules and the people and systems around it."
      />

      <Section title={process.data ? process.data.process.label : "Expense reimbursement"}>
        {process.error && <ErrorMessage error={process.error} onRetry={process.reload} />}
        {!process.data && process.loading && <Loading />}
        {process.data && <ProcessView process={process.data} />}
      </Section>

      <Section title="Explore the graph">
        <NodeExplorer />
      </Section>
    </>
  );
}
