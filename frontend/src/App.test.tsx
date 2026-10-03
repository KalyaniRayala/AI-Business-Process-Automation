import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  auditPage,
  discovery,
  nodeContext,
  processContext,
  recommendations,
  workflowList,
} from "./test/fixtures";
import { mockApi } from "./test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "./test/render";

function routes(extra: Parameters<typeof mockApi>[0] = {}, pending = 0) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(() => ({ body: workflowList([]) }), pending),
    "GET /discovery/workflows": { body: discovery() },
    "GET /automation/recommendations": { body: recommendations([]) },
    "GET /audit-events": { body: auditPage([]) },
    "GET /context/processes/expense_reimbursement": { body: processContext() },
    "GET /context/nodes/employee%3AE102": { body: nodeContext() },
    ...extra,
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("navigation", () => {
  test("has a link for every page", async () => {
    routes();

    renderApp("/");

    const nav = await screen.findByRole("navigation", { name: "Main" });

    const links = within(nav).getAllByRole("link");

    expect(links.map((l) => [l.textContent, l.getAttribute("href")])).toEqual([
      ["Overview", "/"],
      ["Workflows", "/workflows"],
      ["Approvals", "/approvals"],
      ["Discovery", "/discovery"],
      ["Recommendations", "/recommendations"],
      ["Audit trail", "/audit"],
      ["Context graph", "/context"],
      ["System", "/system"],
    ]);
  });

  test("marks the current page", async () => {
    routes();

    renderApp("/discovery");

    const nav = await screen.findByRole("navigation", { name: "Main" });

    expect(within(nav).getByRole("link", { name: "Discovery" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  test("moves between pages without a reload", async () => {
    routes();
    const user = userEvent.setup();

    renderApp("/");

    await user.click(await screen.findByRole("link", { name: "Workflows" }));

    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Context graph" }));

    expect(await screen.findByRole("heading", { name: "Context graph" })).toBeInTheDocument();
  });

  test("an unknown address shows a not-found page with a way back", async () => {
    routes();

    renderApp("/no/such/page");

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the overview" })).toHaveAttribute("href", "/");
  });
});

describe("approvals badge", () => {
  test("shows how many workflows are waiting", async () => {
    routes({}, 3);

    renderApp("/");

    expect(await screen.findByLabelText("3 waiting")).toHaveTextContent("3");
  });

  test("shows no badge when nothing is waiting", async () => {
    routes({}, 0);

    renderApp("/");

    await screen.findByRole("navigation", { name: "Main" });

    await waitFor(() => expect(screen.queryByLabelText(/waiting/)).not.toBeInTheDocument());
  });

  test("a failure to count does not break the page", async () => {
    mockApi({
      "GET /health": healthOk,
      "GET /workflows": () => ({ status: 500, body: { detail: "down" } }),
      "GET /discovery/workflows": { body: discovery() },
      "GET /automation/recommendations": { body: recommendations([]) },
    });

    renderApp("/");

    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/waiting/)).not.toBeInTheDocument();
  });
});

describe("backend health indicator", () => {
  test("shows the backend is online", async () => {
    routes();

    renderApp("/workflows");

    expect(await screen.findByText("Backend online")).toBeInTheDocument();
  });

  test("shows the backend is offline when the health check fails", async () => {
    routes({ "GET /health": { status: 503, body: { detail: "down" } } });

    renderApp("/workflows");

    expect(await screen.findByText("Backend offline")).toBeInTheDocument();
  });

  test("shows the backend is offline when it cannot be reached", async () => {
    mockApi({
      "GET /health": () => {
        throw new TypeError("Failed to fetch");
      },
      "GET /workflows": workflowsRoute(),
    });

    renderApp("/workflows");

    expect(await screen.findByText("Backend offline")).toBeInTheDocument();
  });

  test("checks again periodically and recovers", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    let online = false;

    const api = routes({
      "GET /health": () => (online ? { body: { status: "ok" } } : { status: 503, body: {} }),
    });

    renderApp("/workflows");

    expect(await screen.findByText("Backend offline")).toBeInTheDocument();

    online = true;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(await screen.findByText("Backend online")).toBeInTheDocument();
    expect(api.to("GET", "/health").length).toBeGreaterThanOrEqual(2);
  });

  test("stops checking when the app is closed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    const api = routes();

    const view = renderApp("/workflows");

    await screen.findByText("Backend online");

    const before = api.to("GET", "/health").length;

    view.unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });

    expect(api.to("GET", "/health")).toHaveLength(before);
  });
});
