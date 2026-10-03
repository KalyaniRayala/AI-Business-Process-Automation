import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { auditEvent, auditPage, THREAD } from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

const events = [
  auditEvent({ id: 1, event_type: "workflow_started", message: "Workflow started." }),
  auditEvent({
    id: 2,
    event_type: "node_failed",
    node: "employee",
    status: "failure",
    message: "Node 'employee' reported a failure.",
    error_message: "Employee E999 was not found.",
  }),
  auditEvent({
    id: 3,
    thread_id: null,
    event_type: "connector_sync_completed",
    status: "success",
    message: "Connector entra sync completed.",
  }),
];

type Route = Parameters<typeof mockApi>[0][string];

function routes(route: Route = { body: auditPage(events) }) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(),
    "GET /audit-events": route,
  });
}

describe("audit events", () => {
  test("lists events with their status, workflow and message", async () => {
    routes();

    renderApp("/audit");

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);

    expect(rows).toHaveLength(3);

    expect(rows[0]).toHaveTextContent("Workflow started");
    expect(rows[0]).toHaveTextContent("Info");
    expect(within(rows[0]!).getByRole("link", { name: "11111111" })).toHaveAttribute(
      "href",
      `/workflows/${THREAD}`,
    );

    // A failure shows its error rather than the generic message.
    expect(rows[1]).toHaveTextContent("Node failed");
    expect(rows[1]).toHaveTextContent("employee");
    expect(rows[1]).toHaveTextContent("Failure");
    expect(rows[1]).toHaveTextContent("Employee E999 was not found.");

    // Events not tied to a workflow have no link.
    expect(rows[2]).toHaveTextContent("Connector sync completed");
    expect(within(rows[2]!).queryByRole("link")).not.toBeInTheDocument();
  });

  test("is empty when nothing matches", async () => {
    routes({ body: auditPage([]) });

    renderApp("/audit");

    expect(await screen.findByText("No audit events match.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  test("reports a failure and can retry", async () => {
    let attempts = 0;

    routes(() => {
      attempts += 1;

      return attempts === 1 ? { status: 500, body: { detail: "Audit unavailable." } } : { body: auditPage(events) };
    });
    const user = userEvent.setup();

    renderApp("/audit");

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("Audit unavailable.");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("table")).toBeInTheDocument();
  });
});

describe("filters", () => {
  test("sends the filters that were filled in", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/audit");

    await screen.findByRole("table");

    await user.type(screen.getByLabelText("Workflow ID"), ` ${THREAD} `);
    await user.selectOptions(screen.getByLabelText("Event type"), "node_failed");
    await user.type(screen.getByLabelText("Entity type"), "employee");
    await user.type(screen.getByLabelText("Entity ID"), "E102");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(api.to("GET", "/audit-events")).toHaveLength(2));

    const query = api.to("GET", "/audit-events")[1]!.query;

    expect(query.get("thread_id")).toBe(THREAD);
    expect(query.get("event_type")).toBe("node_failed");
    expect(query.get("entity_type")).toBe("employee");
    expect(query.get("entity_id")).toBe("E102");
    expect(query.get("limit")).toBe("100");
    expect(query.has("after_id")).toBe(false);
  });

  test("blank filters are left out, and clear resets the search", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/audit");

    await screen.findByRole("table");

    await user.type(screen.getByLabelText("Entity ID"), "E102");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(api.to("GET", "/audit-events")).toHaveLength(2));

    expect([...api.to("GET", "/audit-events")[1]!.query.keys()].sort()).toEqual(["entity_id", "limit"]);

    await user.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => expect(api.to("GET", "/audit-events")).toHaveLength(3));

    expect(screen.getByLabelText("Entity ID")).toHaveValue("");
    expect(api.to("GET", "/audit-events")[2]!.query.has("entity_id")).toBe(false);
  });

  test("offers every audit event type", async () => {
    routes();

    renderApp("/audit");

    const select = await screen.findByLabelText("Event type");

    expect(within(select).getByRole("option", { name: "Approval requested" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "Connector sync failed" })).toBeInTheDocument();
    expect(within(select).getAllByRole("option")).toHaveLength(25);        // "Any" + 24 types
  });
});

describe("paging", () => {
  test("loads the next page after the last event shown", async () => {
    const second = [auditEvent({ id: 4, event_type: "workflow_completed", message: "Workflow completed successfully." })];

    const api = routes((request) =>
      request.query.get("after_id") === "3"
        ? { body: auditPage(second, null) }
        : { body: auditPage(events, 3) },
    );
    const user = userEvent.setup();

    renderApp("/audit");

    await screen.findByRole("table");

    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(4);

    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Workflow completed successfully.")).toBeInTheDocument();

    // Earlier events stay, new ones are appended.
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(5);
    expect(api.to("GET", "/audit-events").at(-1)?.query.get("after_id")).toBe("3");

    // No more pages: no button.
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  test("changing the filters starts over from the first page", async () => {
    const api = routes({ body: auditPage(events, 3) });
    const user = userEvent.setup();

    renderApp("/audit");

    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(api.to("GET", "/audit-events")).toHaveLength(2));

    await user.type(screen.getByLabelText("Entity ID"), "E102");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(api.to("GET", "/audit-events")).toHaveLength(3));

    expect(api.to("GET", "/audit-events")[2]!.query.has("after_id")).toBe(false);

    // Only the new first page is shown, not the earlier pages appended to it.
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(4));
  });
});
