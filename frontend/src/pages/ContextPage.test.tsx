import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { nodeContext, processContext } from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

function routes(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /health": healthOk,
    "GET /workflows": workflowsRoute(),
    "GET /context/processes/expense_reimbursement": { body: processContext() },
    "GET /context/nodes/employee%3AE102": { body: nodeContext() },
    ...extra,
  });
}

describe("process view", () => {
  test("shows each step with who performs it and how it is automated", async () => {
    routes();

    renderApp("/context");

    const section = await screen.findByRole("region", { name: "Expense Reimbursement" });
    const rows = within(section).getAllByRole("row").slice(1);

    expect(rows).toHaveLength(3);

    expect(rows[0]).toHaveTextContent("Check expense reimbursement policy");
    expect(rows[0]).toHaveTextContent("Finance");
    // A rule key is shown as its readable label.
    expect(rows[0]).toHaveTextContent("Expenses above $500 require manager approval");
    expect(rows[0]).toHaveTextContent("Policy evaluation node");
    expect(rows[0]).toHaveTextContent("document:data/expense_process.txt#L9");

    expect(rows[1]).toHaveTextContent("Human approval node");
    expect(rows[1]).toHaveTextContent("Partial");
    expect(rows[1]).toHaveTextContent("Email");

    expect(rows[2]).toHaveTextContent("Notify employee");
    expect(rows[2]).toHaveTextContent("Manual");
  });

  test("shows the approval rules with their thresholds", async () => {
    routes();

    renderApp("/context");

    const heading = await screen.findByRole("heading", { name: "Approval rules" });
    const list = heading.nextElementSibling as HTMLElement;

    expect(list).toHaveTextContent("Expenses above $500 require manager approval");
    expect(list).toHaveTextContent("Manager above $500");
    expect(list).toHaveTextContent("Source: document:data/expense_process.txt#L15");
  });

  test("reports a failure to load the process", async () => {
    routes({
      "GET /context/processes/expense_reimbursement": {
        status: 404,
        body: { detail: "Process process:expense_reimbursement was not found." },
      },
    });

    renderApp("/context");

    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
    expect(await screen.findByText("Process process:expense_reimbursement was not found.")).toBeInTheDocument();
  });
});

describe("node explorer", () => {
  test("starts with an example node and lists what is related to it", async () => {
    routes();

    renderApp("/context");

    const explorer = await screen.findByRole("region", { name: "Explore the graph" });

    expect(await within(explorer).findByText("department:finance", { exact: false })).toBeInTheDocument();
    expect(within(explorer).getByRole("button", { name: "Finance" })).toBeInTheDocument();

    const row = within(explorer).getByRole("button", { name: "Finance" }).closest("tr")!;

    expect(row).toHaveTextContent("Department");
    expect(row).toHaveTextContent("employee:E102 member of department:finance");
    expect(row).toHaveTextContent("Curated");
  });

  test("looks up another node, with its key encoded in the path", async () => {
    const api = routes({
      "GET /context/nodes/document%3Adata/expense_process.txt": {
        body: nodeContext({
          node: {
            node_key: "document:data/expense_process.txt",
            node_type: "document",
            label: "expense_process.txt",
            properties: null,
            source_type: "document",
            source_ref: null,
            is_active: true,
          },
          neighbors: [],
          count: 0,
        }),
      },
    });
    const user = userEvent.setup();

    renderApp("/context");

    const explorer = await screen.findByRole("region", { name: "Explore the graph" });

    const input = within(explorer).getByLabelText("Node key");

    await user.clear(input);
    await user.type(input, "  document:data/expense_process.txt  ");
    await user.click(within(explorer).getByRole("button", { name: "Explore" }));

    expect(await within(explorer).findByText("No related nodes.")).toBeInTheDocument();
    expect(within(explorer).getByText("expense_process.txt")).toBeInTheDocument();

    expect(api.to("GET", "/context/nodes/document%3Adata/expense_process.txt")).toHaveLength(1);
  });

  test("clicking a related node follows the link", async () => {
    const api = routes({
      "GET /context/nodes/department%3Afinance": {
        body: nodeContext({
          node: {
            node_key: "department:finance",
            node_type: "department",
            label: "Finance",
            properties: null,
            source_type: "system_of_record",
            source_ref: null,
            is_active: true,
          },
          neighbors: [],
          count: 0,
        }),
      },
    });
    const user = userEvent.setup();

    renderApp("/context");

    const explorer = await screen.findByRole("region", { name: "Explore the graph" });

    await user.click(await within(explorer).findByRole("button", { name: "Finance" }));

    expect(await within(explorer).findByText("department:finance")).toBeInTheDocument();
    expect(within(explorer).getByLabelText("Node key")).toHaveValue("department:finance");
    expect(api.to("GET", "/context/nodes/department%3Afinance")).toHaveLength(1);
  });

  test("passes the hops and direction to the API", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/context");

    const explorer = await screen.findByRole("region", { name: "Explore the graph" });

    await within(explorer).findByRole("button", { name: "Finance" });

    await user.selectOptions(within(explorer).getByLabelText("Hops"), "3");
    await user.selectOptions(within(explorer).getByLabelText("Direction"), "out");

    await within(explorer).findByRole("button", { name: "Finance" });

    const last = api.to("GET", "/context/nodes/employee%3AE102").at(-1)!;

    expect(last.query.get("depth")).toBe("3");
    expect(last.query.get("direction")).toBe("out");
  });

  test("an unknown node is reported", async () => {
    routes({
      "GET /context/nodes/employee%3AE999": { status: 404, body: { detail: "Context node employee:E999 was not found." } },
    });
    const user = userEvent.setup();

    renderApp("/context");

    const explorer = await screen.findByRole("region", { name: "Explore the graph" });

    await within(explorer).findByRole("button", { name: "Finance" });

    const input = within(explorer).getByLabelText("Node key");

    await user.clear(input);
    await user.type(input, "employee:E999");
    await user.click(within(explorer).getByRole("button", { name: "Explore" }));

    const alert = await within(explorer).findByRole("alert");

    expect(alert).toHaveTextContent("Node not found");
    expect(alert).toHaveTextContent("Context node employee:E999 was not found.");
  });

  test("says when the result was truncated and when a node is inactive", async () => {
    routes({
      "GET /context/nodes/employee%3AE102": {
        body: nodeContext({
          truncated: true,
          node: { ...nodeContext().node, is_active: false },
        }),
      },
    });

    renderApp("/context");

    const explorer = await screen.findByRole("region", { name: "Explore the graph" });

    expect(await within(explorer).findByText("Inactive")).toBeInTheDocument();
    expect(within(explorer).getByText("The result was truncated at the neighbor limit.")).toBeInTheDocument();
  });
});
