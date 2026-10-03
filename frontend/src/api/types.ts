// Types mirroring the backend's Pydantic response models (backend/app/main.py).

// ---------------------------------------------------------------- workflows

export const WORKFLOW_STATUSES = [
  "running",
  "awaiting_approval",
  "success",
  "rejected",
  "failed",
] as const;

export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];

export interface WorkflowSummary {
  thread_id: string;
  question: string;
  employee_id: string | null;
  expense_amount: number | null;
  status: string;
  approval_status: string | null;
  failed_node: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowList {
  workflows: WorkflowSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface EmployeeRecord {
  employee_id: string;
  name: string;
  department: string;
  manager: string;
  annual_budget?: number;
}

/** What the context graph adds for the approver (backend CG6). */
export interface BusinessContextSummary {
  departments: string[];
  managers: { name: string; roles: string[] }[];
  required_approvals: string[] | null;
  rules: { rule: string; description: string; evidence: string[] }[];
  policy_check: {
    consistent: boolean;
    graph_approvers: string[];
    policy_approvers: string[];
  } | null;
}

export interface ApprovalRequest {
  type: string;
  message: string;
  employee?: { success?: boolean; employee?: EmployeeRecord; error?: string };
  policy?: {
    success?: boolean;
    expense_amount?: number;
    approval?: string;
    error?: string;
  };
  business_context?: BusinessContextSummary;
}

export interface WorkflowResponse {
  thread_id: string;
  status: string;
  approval_status: string | null;
  approval_request: ApprovalRequest | null;
  answer: string | null;
  action_result: Record<string, unknown> | null;
  error_message: string | null;
}

export interface DecisionRequest {
  decision: "approve" | "reject";
  approver?: string;
  comment?: string;
}

// -------------------------------------------------------------------- audit

export interface AuditEvent {
  id: number;
  event_id: string;
  occurred_at: string;
  thread_id: string | null;
  event_type: string;
  actor_type: string;
  actor_id: string | null;
  node: string | null;
  entity_type: string | null;
  entity_id: string | null;
  status: string;
  message: string;
  details: Record<string, unknown> | null;
  error_type: string | null;
  error_message: string | null;
}

export interface AuditPage {
  events: AuditEvent[];
  count: number;
  limit: number;
  next_after_id: number | null;
}

export interface AuditHistory extends AuditPage {
  thread_id: string;
}

export const AUDIT_EVENT_TYPES = [
  "workflow_started",
  "workflow_resumed",
  "workflow_completed",
  "workflow_rejected",
  "workflow_failed",
  "workflow_recovery",
  "workflow_error",
  "node_started",
  "node_completed",
  "node_failed",
  "employee_lookup",
  "policy_evaluated",
  "document_retrieval",
  "tool_execution",
  "approval_not_required",
  "approval_requested",
  "approval_approved",
  "approval_rejected",
  "approval_decision_rejected",
  "business_action_executed",
  "business_action_failed",
  "connector_sync_started",
  "connector_sync_completed",
  "connector_sync_failed",
] as const;

// ------------------------------------------------------------ context graph

export interface ContextNode {
  node_key: string;
  node_type: string;
  label: string;
  properties: Record<string, unknown> | null;
  source_type: string;
  source_ref: string | null;
  is_active: boolean;
}

export interface ContextEdge {
  from_key: string;
  edge_type: string;
  to_key: string;
  properties: Record<string, unknown> | null;
  source_type: string;
  source_ref: string | null;
  confidence: number | null;
}

export interface ContextNeighbor {
  depth: number;
  node: ContextNode;
  via: ContextEdge;
}

export interface NodeContext {
  node: ContextNode;
  depth: number;
  direction: string;
  count: number;
  truncated: boolean;
  neighbors: ContextNeighbor[];
}

export interface ProcessStep {
  order: number | null;
  step: ContextNode;
  next_steps: string[];
  performed_by: ContextNode[];
  uses_systems: ContextNode[];
  governed_by: string[];
  implemented_by: {
    component: ContextNode;
    properties: Record<string, unknown> | null;
  }[];
  evidence: string[];
}

export interface PolicyRule {
  rule: ContextNode;
  approvers: { role: ContextNode; threshold: number | null }[];
  policies: string[];
  evidence: string[];
}

export interface ProcessContext {
  process: ContextNode;
  steps: ProcessStep[];
  policy_rules: PolicyRule[];
  evidence: string[];
}

// ---------------------------------------------------------------- discovery

export interface DurationSummary {
  count: number;
  mean: number;
  median: number;
  p95: number;
  max: number;
}

export interface WorkflowVariant {
  variant_id: string;
  path: string[][];
  path_text: string;
  human_approval: boolean;
  workflows: number;
  share: number;
  outcomes: Record<string, number>;
  cycle_time_seconds: DurationSummary | null;
  sample_thread_ids: string[];
}

export interface StepStats {
  node: string;
  workflows: number;
  executions: number;
  failures: number;
  failure_rate: number;
  duration_seconds: DurationSummary | null;
  modeled_steps: number[];
}

export interface ApprovalStats {
  workflows: number;
  required_approval: number;
  approval_rate_of_workflows: number;
  approved: number;
  rejected: number;
  pending: number;
  rejection_rate: number;
  wait_seconds: DurationSummary | null;
}

export interface DepartmentVolume {
  department: string;
  workflows: number;
  total_amount: number;
  required_approval: number;
  rejected: number;
}

export interface AmountBand {
  band: string;
  above: number | null;
  up_to: number | null;
  workflows: number;
  required_approval: number;
  rejected: number;
}

export interface ModelStep {
  order: number | null;
  step: string;
  label: string;
  performed_by: string[];
  uses_systems: string[];
  automation: "automated" | "partial" | "manual";
  implemented_by: { component: string; label: string; coverage: string }[];
  workflows_observed: number | null;
  evidence: string[];
}

export interface DiscoveryWindow {
  workflows_analyzed: number;
  limit: number;
  since: string | null;
  until: string | null;
  first_started_at: string | null;
  last_started_at: string | null;
}

export interface Discovery {
  window: DiscoveryWindow;
  outcomes: Record<string, number>;
  variants: WorkflowVariant[];
  steps: StepStats[];
  approvals: ApprovalStats;
  volume: {
    workflows_with_run_record: number;
    workflows_without_run_record: number;
    by_department: DepartmentVolume[];
    by_amount_band: AmountBand[];
  };
  process_model: {
    process: string;
    label: string;
    steps: ModelStep[];
    automation_gaps: ModelStep[];
    unmapped_nodes: string[];
    thresholds: number[];
  } | null;
}

// ---------------------------------------------------------- recommendations

export type Priority = "low" | "medium" | "high";

export const PRIORITIES: Priority[] = ["low", "medium", "high"];

export interface Recommendation {
  recommendation_id: string;
  type:
    | "automate_manual_step"
    | "complete_partial_automation"
    | "reduce_approval_wait"
    | "reduce_step_failures";
  title: string;
  summary: string;
  target: { kind: string; key: string; label: string; order: number | null };
  priority: Priority;
  score: number;
  scores: { impact: number; feasibility: number; confidence: number };
  confidence: Priority;
  effort: Priority;
  rationale: string[];
  metrics: Record<string, unknown>;
  suggested_actions: string[];
  evidence: string[];
  requires_human_decision: boolean;
}

export interface Recommendations {
  basis: {
    process: string;
    window: DiscoveryWindow;
    outcomes: Record<string, number>;
  };
  summary: {
    total: number;
    by_priority: Record<string, number>;
    by_type: Record<string, number>;
  };
  recommendations: Recommendation[];
  warnings: string[];
  method: {
    formula: string;
    weights: Record<string, number>;
    priority_thresholds: Record<string, number>;
    volume_full_scale: number;
    notes: string[];
  };
}

// ============================================================
// OBSERVABILITY
// ============================================================

/** One readiness probe: can this process actually serve a request? */
export interface ReadinessCheck {
  name: string;
  status: "ok" | "degraded" | "failed";
  essential: boolean;
  detail: string;
  duration_seconds: number;
}

export interface Readiness {
  status: "ok" | "degraded" | "failed";
  checks: ReadinessCheck[];
  uptime_seconds: number;
}

/** A timed operation kept in the backend's in-memory ring buffer. */
export interface OperationRecord {
  operation: string;
  status: string;
  duration_seconds: number;
  started_at: string;
  attributes: Record<string, unknown>;
  request_id?: string;
  thread_id?: string;
  error?: string;
}

export interface RouteCount {
  route: string;
  method: string;
  status: string;
  count: number;
}

export interface NodeStats {
  node: string;
  total: number;
  failed: number;
  average_seconds: number;
}

/**
 * The live view of one backend process. These numbers are per process
 * and reset when it restarts; the audit trail is the durable record.
 */
export interface ObservabilitySummary {
  status: string;
  started_at: string;
  uptime_seconds: number;
  requests: {
    total: number;
    failed: number;
    in_progress: number;
    average_seconds: number;
    by_route: RouteCount[];
  };
  workflows: {
    by_outcome: Record<string, number>;
    average_seconds: Record<string, number>;
    nodes: NodeStats[];
    awaiting_approval: number | null;
  };
  model_calls: {
    total: number;
    by_operation: { provider: string; operation: string; status: string; count: number }[];
    average_seconds: Record<string, number>;
  };
  errors: {
    total: number;
    by_kind: { kind: string; where: string; count: number }[];
    recent: OperationRecord[];
  };
  slowest_operations: OperationRecord[];
  recent_operations: OperationRecord[];
}
