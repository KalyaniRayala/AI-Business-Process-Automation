import type { BusinessContextSummary } from "../api/types";
import { Badge } from "./ui";

/**
 * What the context graph adds for an approver: department, manager, who
 * must approve, the rules that apply and their document evidence. The policy
 * tool still decides whether approval is needed; this is for information.
 */
export function BusinessContextView({ summary }: { summary: BusinessContextSummary }) {
  const check = summary.policy_check;

  return (
    <div className="context-summary">
      <h4>Context graph</h4>

      {check && !check.consistent && (
        <div className="alert alert--warning" role="alert">
          <strong>The context graph and the policy tool disagree.</strong>
          <p>
            The graph requires {check.graph_approvers.join(", ") || "no one"}; the policy tool
            requires {check.policy_approvers.join(", ") || "no one"}. The policy tool decides.
          </p>
        </div>
      )}

      <dl className="facts">
        <div>
          <dt>Department</dt>
          <dd>{summary.departments.join(", ") || "Not recorded"}</dd>
        </div>
        <div>
          <dt>Manager</dt>
          <dd>
            {summary.managers.length === 0
              ? "Not recorded"
              : summary.managers
                  .map((m) => (m.roles.length ? `${m.name} (${m.roles.join(", ")})` : m.name))
                  .join(", ")}
          </dd>
        </div>
        <div>
          <dt>Approval required from</dt>
          <dd>
            {summary.required_approvals === null ? (
              "Not evaluated"
            ) : summary.required_approvals.length === 0 ? (
              "No one"
            ) : (
              <span className="chips">
                {summary.required_approvals.map((role) => (
                  <Badge key={role} tone="warning">
                    {role}
                  </Badge>
                ))}
              </span>
            )}
          </dd>
        </div>
        {check?.consistent && (
          <div>
            <dt>Policy check</dt>
            <dd>
              <Badge tone="success">Graph and policy tool agree</Badge>
            </dd>
          </div>
        )}
      </dl>

      {summary.rules.length > 0 && (
        <ul className="rules">
          {summary.rules.map((rule) => (
            <li key={rule.rule}>
              <span>{rule.description}</span>
              {rule.evidence.length > 0 && (
                <small className="muted"> Source: {rule.evidence.join(", ")}</small>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
