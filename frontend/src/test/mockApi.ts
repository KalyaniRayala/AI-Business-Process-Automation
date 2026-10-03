import { vi } from "vitest";

export interface MockRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

export interface MockResult {
  status?: number;
  body?: unknown;
  /** Raw text instead of JSON, for non-JSON error pages. */
  text?: string;
}

type Route = MockResult | ((request: MockRequest) => MockResult | Promise<MockResult>);

/** Calls no route matched; any entry fails the test (see setup.ts). */
export const unmocked: string[] = [];

export function assertNoUnmockedCalls() {
  const calls = unmocked.splice(0);

  if (calls.length > 0) {
    throw new Error(`Unmocked API calls: ${calls.join(", ")}`);
  }
}

/**
 * Replaces fetch with a router keyed "METHOD /path" (the "/api" prefix is
 * stripped). A route is a fixed result or a function of the request.
 * Returns the recorded calls so tests can assert on what was sent.
 */
export function mockApi(routes: Record<string, Route>) {
  const calls: MockRequest[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api/, "");

    const request: MockRequest = {
      method,
      path,
      query: url.searchParams,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };

    calls.push(request);

    const route = routes[`${method} ${path}`];

    if (route === undefined) {
      unmocked.push(`${method} ${path}`);

      return new Response(JSON.stringify({ detail: `No mock for ${method} ${path}` }), {
        status: 599,
      });
    }

    const result = typeof route === "function" ? await route(request) : route;
    const status = result.status ?? 200;

    return new Response(
      result.text !== undefined ? result.text : JSON.stringify(result.body ?? {}),
      { status, headers: { "Content-Type": "application/json" } },
    );
  });

  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    fetchMock,
    /** Calls to "METHOD /path", in order. */
    to: (method: string, path: string) =>
      calls.filter((call) => call.method === method && call.path === path),
  };
}
