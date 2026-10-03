import type {
  AuditHistory,
  AuditPage,
  DecisionRequest,
  Discovery,
  NodeContext,
  ObservabilitySummary,
  ProcessContext,
  Readiness,
  Recommendations,
  WorkflowList,
  WorkflowResponse,
} from "./types";

/**
 * All requests go to `${API_BASE}/...`. By default that is "/api", which the
 * Vite dev/preview server forwards to the backend (see vite.config.ts); set
 * VITE_API_BASE_URL to call a backend directly or to match a reverse proxy.
 */
export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(
  /\/+$/,
  "",
);

/** An API failure with a message that is safe to show to the user. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type QueryValue = string | number | boolean | null | undefined | readonly string[];

export type Query = Record<string, QueryValue>;

/** Builds "?a=1&b=2"; skips empty values and repeats array parameters. */
export function buildQuery(query?: Query): string {
  if (!query) return "";

  const params = new URLSearchParams();

  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;

    if (Array.isArray(value)) {
      for (const item of value) params.append(name, item);
    } else {
      params.append(name, String(value));
    }
  }

  const text = params.toString();

  return text ? `?${text}` : "";
}

/** FastAPI's `detail` is a string, or a list of validation problems. */
export function describeDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        const where = Array.isArray((item as { loc?: unknown }).loc)
          ? ((item as { loc: unknown[] }).loc.filter((p) => p !== "body" && p !== "query").join("."))
          : "";
        const msg = String((item as { msg: unknown }).msg);
        return where ? `${where}: ${msg}` : msg;
      }
      return String(item);
    });

    return parts.join("; ") || null;
  }

  return null;
}

async function failureMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();

    if (body && typeof body === "object" && "detail" in body) {
      const text = describeDetail((body as { detail: unknown }).detail);
      if (text) return text;
    }
  } catch {
    // Not JSON: fall through to the generic message.
  }

  return `The server returned an error (HTTP ${response.status}).`;
}

interface RequestOptions {
  method?: "GET" | "POST";
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  /**
   * Error statuses whose body is still the answer. The readiness probe
   * replies 503 when a check fails, and that response is what we want to
   * show, not an error message hiding it.
   */
  acceptStatuses?: readonly number[];
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", query, body, signal, acceptStatuses = [] } = options;

  let response: Response;

  try {
    response = await fetch(`${API_BASE}${path}${buildQuery(query)}`, {
      method,
      signal,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;

    throw new ApiError(
      0,
      "Cannot reach the API. Check that the backend is running and try again.",
    );
  }

  if (!response.ok && !acceptStatuses.includes(response.status)) {
    throw new ApiError(response.status, await failureMessage(response));
  }

  return (await response.json()) as T;
}

/** Context node keys contain ":" and "/" (the backend accepts both in the path). */
export function encodeNodeKey(key: string): string {
  return key.split("/").map(encodeURIComponent).join("/");
}

// ---------------------------------------------------------------- endpoints

export interface WindowParams {
  limit?: number;
  since?: string;
  until?: string;
}

export const api = {
  health: (signal?: AbortSignal) => request<{ status: string }>("/health", { signal }),

  listWorkflows: (
    params: { status?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => request<WorkflowList>("/workflows", { query: params, signal }),

  startWorkflow: (question: string) =>
    request<WorkflowResponse>("/workflows", { method: "POST", body: { question } }),

  getWorkflow: (threadId: string, signal?: AbortSignal) =>
    request<WorkflowResponse>(`/workflows/${encodeURIComponent(threadId)}`, { signal }),

  submitDecision: (threadId: string, decision: DecisionRequest) =>
    request<WorkflowResponse>(`/workflows/${encodeURIComponent(threadId)}/decision`, {
      method: "POST",
      body: decision,
    }),

  workflowAudit: (
    threadId: string,
    params: { limit?: number; after_id?: number } = {},
    signal?: AbortSignal,
  ) =>
    request<AuditHistory>(`/workflows/${encodeURIComponent(threadId)}/audit`, {
      query: params,
      signal,
    }),

  searchAudit: (
    params: {
      thread_id?: string;
      event_type?: string;
      entity_type?: string;
      entity_id?: string;
      limit?: number;
      after_id?: number;
    },
    signal?: AbortSignal,
  ) => request<AuditPage>("/audit-events", { query: params, signal }),

  process: (processKey: string, signal?: AbortSignal) =>
    request<ProcessContext>(`/context/processes/${encodeURIComponent(processKey)}`, { signal }),

  contextNode: (
    nodeKey: string,
    params: { depth?: number; direction?: string; edge_type?: string[]; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    request<NodeContext>(`/context/nodes/${encodeNodeKey(nodeKey)}`, {
      query: params,
      signal,
    }),

  discovery: (params: WindowParams = {}, signal?: AbortSignal) =>
    request<Discovery>("/discovery/workflows", { query: { ...params }, signal }),

  recommendations: (
    params: WindowParams & { min_priority?: string } = {},
    signal?: AbortSignal,
  ) => request<Recommendations>("/automation/recommendations", { query: { ...params }, signal }),

  // 503 means "not ready", and its body says which check failed.
  readiness: (signal?: AbortSignal) =>
    request<Readiness>("/health/ready", { signal, acceptStatuses: [503] }),

  observability: (params: { limit?: number } = {}, signal?: AbortSignal) =>
    request<ObservabilitySummary>("/observability/summary", { query: params, signal }),
};
