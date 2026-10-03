import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppRoutes } from "../App";
import { workflowList } from "./fixtures";
import type { MockRequest, MockResult } from "./mockApi";

export type Entry = string | { pathname: string; state?: unknown };

/** Renders the whole app (layout, navigation and routes) at a path. */
export function renderApp(entry: Entry = "/") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

/**
 * The layout asks for the number of workflows awaiting approval
 * (status=awaiting_approval&limit=1) on every page. This route answers that
 * call with `navPending` and hands every other call to `handler`.
 */
export function workflowsRoute(
  handler: (request: MockRequest) => MockResult = () => ({ body: workflowList([]) }),
  navPending = 0,
) {
  return (request: MockRequest): MockResult => {
    if (request.query.get("status") === "awaiting_approval" && request.query.get("limit") === "1") {
      return { body: workflowList([], { total: navPending, limit: 1 }) };
    }

    return handler(request);
  };
}

export const healthOk = { status: 200, body: { status: "ok" } } as const;
