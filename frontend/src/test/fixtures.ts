import type {
  ApprovalRequest,
  AuditEvent,
  AuditPage,
  Discovery,
  NodeContext,
  ObservabilitySummary,
  OperationRecord,
  ProcessContext,
  Readiness,
  Recommendation,
  Recommendations,
  WorkflowList,
  WorkflowResponse,
  WorkflowSummary,
} from "../api/types";

export const THREAD = "11111111-2222-3333-4444-555555555555";

export function workflowSummary(overrides: Partial<WorkflowSummary> = {}): WorkflowSummary {
  return {
    thread_id: THREAD,
    question: "For employee E102, process a $750 expense.",
    employee_id: "E102",
    expense_amount: 750,
    status: "awaiting_approval",
    approval_status: null,
    failed_node: null,
    error_message: null,
    created_at: "2026-10-01T09:00:00+00:00",
    updated_at: "2026-10-01T09:00:05+00:00",
    ...overrides,
  };
}

export function workflowList(
  workflows: WorkflowSummary[] = [workflowSummary()],
  extra: Partial<WorkflowList> = {},
): WorkflowList {
  return { workflows, total: workflows.length, limit: 20, offset: 0, ...extra };
}

export function approvalRequest(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    type: "approval_required",
    message: "This expense requires approval before the workflow can continue.",
    employee: {
      success: true,
      employee: {
        employee_id: "E102",
        name: "Meera",
        department: "Finance",
        manager: "Priya",
        annual_budget: 60000,
      },
    },
    policy: { success: true, expense_amount: 750, approval: "Manager approval required." },
    business_context: {
      departments: ["Finance"],
      managers: [{ name: "Priya", roles: ["Manager"] }],
      required_approvals: ["Manager"],
      rules: [
        {
          rule: "policy_rule:expense_approval_over_500",
          description: "Expenses above $500 require manager approval",
          evidence: ["document:data/expense_process.txt#L15"],
        },
      ],
      policy_check: {
        consistent: true,
        graph_approvers: ["Manager"],
        policy_approvers: ["Manager"],
      },
    },
    ...overrides,
  };
}

export function workflowResponse(overrides: Partial<WorkflowResponse> = {}): WorkflowResponse {
  return {
    thread_id: THREAD,
    status: "awaiting_approval",
    approval_status: null,
    approval_request: approvalRequest(),
    answer: null,
    action_result: null,
    error_message: null,
    ...overrides,
  };
}

export function auditEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 1,
    event_id: "evt-1",
    occurred_at: "2026-10-01T09:00:00+00:00",
    thread_id: THREAD,
    event_type: "workflow_started",
    actor_type: "system",
    actor_id: null,
    node: null,
    entity_type: null,
    entity_id: null,
    status: "info",
    message: "Workflow started.",
    details: null,
    error_type: null,
    error_message: null,
    ...overrides,
  };
}

export function auditPage(events: AuditEvent[] = [auditEvent()], nextAfterId: number | null = null): AuditPage {
  return { events, count: events.length, limit: 100, next_after_id: nextAfterId };
}

const summary = (count: number, mean: number) => ({
  count,
  mean,
  median: mean,
  p95: mean * 2,
  max: mean * 3,
});

export function discovery(overrides: Partial<Discovery> = {}): Discovery {
  return {
    window: {
      workflows_analyzed: 10,
      limit: 1000,
      since: null,
      until: null,
      first_started_at: "2026-10-01T08:00:00+00:00",
      last_started_at: "2026-10-01T09:30:00+00:00",
    },
    outcomes: {
      completed: 6,
      rejected: 1,
      failed: 1,
      error: 0,
      awaiting_approval: 2,
      in_progress: 0,
    },
    variants: [
      {
        variant_id: "abc123",
        path: [["employee", "policy", "rag"], ["approval"], ["action"], ["final"]],
        path_text: "employee+policy+rag -> approval -> action -> final",
        human_approval: false,
        workflows: 6,
        share: 0.6,
        outcomes: { completed: 6 },
        cycle_time_seconds: summary(6, 2.5),
        sample_thread_ids: [THREAD],
      },
      {
        variant_id: "def456",
        path: [["employee", "policy", "rag"], ["recovery"]],
        path_text: "employee+policy+rag -> recovery",
        human_approval: false,
        workflows: 1,
        share: 0.1,
        outcomes: { failed: 1 },
        cycle_time_seconds: null,
        sample_thread_ids: [],
      },
    ],
    steps: [
      {
        node: "employee",
        workflows: 10,
        executions: 10,
        failures: 2,
        failure_rate: 0.2,
        duration_seconds: summary(10, 0.3),
        modeled_steps: [5],
      },
      {
        node: "final",
        workflows: 8,
        executions: 8,
        failures: 0,
        failure_rate: 0,
        duration_seconds: null,
        modeled_steps: [],
      },
    ],
    approvals: {
      workflows: 10,
      required_approval: 4,
      approval_rate_of_workflows: 0.4,
      approved: 2,
      rejected: 1,
      pending: 1,
      rejection_rate: 0.3333,
      wait_seconds: summary(3, 120),
    },
    volume: {
      workflows_with_run_record: 10,
      workflows_without_run_record: 0,
      by_department: [
        { department: "Finance", workflows: 6, total_amount: 4500, required_approval: 3, rejected: 1 },
      ],
      by_amount_band: [
        { band: "up to 500", above: null, up_to: 500, workflows: 6, required_approval: 0, rejected: 0 },
        { band: "above 500 up to 5000", above: 500, up_to: 5000, workflows: 4, required_approval: 4, rejected: 1 },
      ],
    },
    process_model: {
      process: "process:expense_reimbursement",
      label: "Expense Reimbursement",
      steps: [
        {
          order: 3,
          step: "process_step:expense_reimbursement:03",
          label: "Validate receipt",
          performed_by: ["Finance"],
          uses_systems: [],
          automation: "manual",
          implemented_by: [],
          workflows_observed: null,
          evidence: ["document:data/expense_process.txt#L7"],
        },
        {
          order: 4,
          step: "process_step:expense_reimbursement:04",
          label: "Check expense reimbursement policy",
          performed_by: ["Finance"],
          uses_systems: [],
          automation: "automated",
          implemented_by: [
            { component: "agent_node:policy", label: "Policy evaluation node", coverage: "full" },
          ],
          workflows_observed: 10,
          evidence: [],
        },
        {
          order: 8,
          step: "process_step:expense_reimbursement:08",
          label: "Finance approval for expenses above $5,000",
          performed_by: ["Finance"],
          uses_systems: [],
          automation: "partial",
          implemented_by: [
            { component: "agent_node:approval", label: "Human approval node", coverage: "partial" },
          ],
          workflows_observed: 4,
          evidence: [],
        },
      ],
      automation_gaps: [],
      unmapped_nodes: ["final", "recovery"],
      thresholds: [500, 5000],
    },
    ...overrides,
  };
}

export function recommendation(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    recommendation_id: "automate_manual_step:process_step:expense_reimbursement:10",
    type: "automate_manual_step",
    title: "Automate: Notify employee of the final decision",
    summary: "Automate the manual step 'Notify employee' (notification); it touches all 10 observed claim(s).",
    target: {
      kind: "process_step",
      key: "process_step:expense_reimbursement:10",
      label: "Notify employee of the final decision",
      order: 10,
    },
    priority: "high",
    score: 84.5,
    scores: { impact: 0.8, feasibility: 0.9, confidence: 0.6 },
    confidence: "medium",
    effort: "low",
    rationale: ["'Notify employee' is a manual step: no workflow node implements it."],
    metrics: {},
    suggested_actions: ["Send the message automatically when the workflow finishes."],
    evidence: ["document:data/expense_process.txt#L21"],
    requires_human_decision: false,
    ...overrides,
  };
}

export function recommendations(
  items: Recommendation[] = [recommendation()],
  overrides: Partial<Recommendations> = {},
): Recommendations {
  return {
    basis: {
      process: "process:expense_reimbursement",
      window: discovery().window,
      outcomes: discovery().outcomes,
    },
    summary: {
      total: items.length,
      by_priority: {
        high: items.filter((i) => i.priority === "high").length,
        medium: items.filter((i) => i.priority === "medium").length,
        low: items.filter((i) => i.priority === "low").length,
      },
      by_type: {},
    },
    recommendations: items,
    warnings: [],
    method: {
      formula: "score = 100 * (0.5 * impact + 0.3 * feasibility + 0.2 * confidence)",
      weights: { impact: 0.5, feasibility: 0.3, confidence: 0.2 },
      priority_thresholds: { high: 70, medium: 45 },
      volume_full_scale: 1000,
      notes: ["Recommendations are advice; nothing is changed or executed."],
    },
    ...overrides,
  };
}

const node = (key: string, label: string, type: string) => ({
  node_key: key,
  node_type: type,
  label,
  properties: null,
  source_type: "curated",
  source_ref: null,
  is_active: true,
});

export function processContext(): ProcessContext {
  return {
    process: node("process:expense_reimbursement", "Expense Reimbursement", "process"),
    steps: [
      {
        order: 4,
        step: node("process_step:expense_reimbursement:04", "Check expense reimbursement policy", "process_step"),
        next_steps: [],
        performed_by: [node("role:finance", "Finance", "role")],
        uses_systems: [],
        governed_by: ["policy_rule:expense_approval_over_500"],
        implemented_by: [
          { component: node("agent_node:policy", "Policy evaluation node", "agent_node"), properties: null },
        ],
        evidence: ["document:data/expense_process.txt#L9"],
      },
      {
        order: 8,
        step: node("process_step:expense_reimbursement:08", "Finance approval", "process_step"),
        next_steps: [],
        performed_by: [],
        uses_systems: [node("system:email", "Email", "system")],
        governed_by: [],
        implemented_by: [
          {
            component: node("agent_node:approval", "Human approval node", "agent_node"),
            properties: { coverage: "partial" },
          },
        ],
        evidence: [],
      },
      {
        order: 10,
        step: node("process_step:expense_reimbursement:10", "Notify employee", "process_step"),
        next_steps: [],
        performed_by: [],
        uses_systems: [],
        governed_by: [],
        implemented_by: [],
        evidence: [],
      },
    ],
    policy_rules: [
      {
        rule: node("policy_rule:expense_approval_over_500", "Expenses above $500 require manager approval", "policy_rule"),
        approvers: [{ role: node("role:manager", "Manager", "role"), threshold: 500 }],
        policies: [],
        evidence: ["document:data/expense_process.txt#L15"],
      },
    ],
    evidence: [],
  };
}

export function nodeContext(overrides: Partial<NodeContext> = {}): NodeContext {
  return {
    node: node("employee:E102", "E102", "employee"),
    depth: 1,
    direction: "both",
    count: 1,
    truncated: false,
    neighbors: [
      {
        depth: 1,
        node: node("department:finance", "Finance", "department"),
        via: {
          from_key: "employee:E102",
          edge_type: "member_of",
          to_key: "department:finance",
          properties: null,
          source_type: "system_of_record",
          source_ref: null,
          confidence: null,
        },
      },
    ],
    ...overrides,
  };
}

// ---------------------------------------------------------- observability

export function readiness(overrides: Partial<Readiness> = {}): Readiness {
  return {
    status: "ok",
    uptime_seconds: 3_725,
    checks: [
      {
        name: "database",
        status: "ok",
        essential: true,
        detail: "postgresql reachable",
        duration_seconds: 0.004,
      },
      {
        name: "vector_store",
        status: "ok",
        essential: true,
        detail: "42 chunks",
        duration_seconds: 0.002,
      },
      {
        name: "model_provider",
        status: "ok",
        essential: true,
        detail: "api key configured",
        duration_seconds: 0.0,
      },
      {
        name: "workflow_state",
        status: "ok",
        essential: false,
        detail: "PostgresSaver (durable)",
        duration_seconds: 0.0,
      },
    ],
    ...overrides,
  };
}

export function operationRecord(
  overrides: Partial<OperationRecord> = {},
): OperationRecord {
  return {
    operation: "cohere.chat",
    status: "success",
    duration_seconds: 1.25,
    started_at: "2026-10-02T09:00:00+00:00",
    attributes: { provider: "cohere", operation: "chat" },
    request_id: "req-1",
    ...overrides,
  };
}

export function observabilitySummary(
  overrides: Partial<ObservabilitySummary> = {},
): ObservabilitySummary {
  return {
    status: "ok",
    started_at: "2026-10-02T08:00:00+00:00",
    uptime_seconds: 3_725,
    requests: {
      total: 120,
      failed: 3,
      in_progress: 0,
      average_seconds: 0.042,
      by_route: [
        { route: "/workflows", method: "GET", status: "200", count: 80 },
        { route: "/workflows/{thread_id}", method: "GET", status: "404", count: 3 },
      ],
    },
    workflows: {
      by_outcome: { success: 7, awaiting_approval: 2 },
      average_seconds: { run: 2.5, resume: 1.1 },
      nodes: [
        { node: "rag", total: 9, failed: 1, average_seconds: 0.8 },
        { node: "policy", total: 9, failed: 0, average_seconds: 0.01 },
      ],
      awaiting_approval: 2,
    },
    model_calls: {
      total: 18,
      by_operation: [
        { provider: "cohere", operation: "chat", status: "success", count: 9 },
        { provider: "cohere", operation: "embed", status: "success", count: 9 },
      ],
      average_seconds: { chat: 1.4, embed: 0.3 },
    },
    errors: {
      total: 1,
      by_kind: [{ kind: "ValueError", where: "http", count: 1 }],
      recent: [
        operationRecord({
          operation: "workflow.node.rag",
          status: "error",
          error: "ValueError: boom",
        }),
      ],
    },
    slowest_operations: [operationRecord({ operation: "cohere.chat", duration_seconds: 4.2 })],
    recent_operations: [operationRecord()],
    ...overrides,
  };
}
