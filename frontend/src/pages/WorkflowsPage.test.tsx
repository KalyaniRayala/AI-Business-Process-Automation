import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import {
  auditPage,
  THREAD,
  workflowList,
  workflowResponse,
  workflowSummary,
} from "../test/fixtures";
import { mockApi } from "../test/mockApi";
import { healthOk, renderApp, workflowsRoute } from "../test/render";

const QUESTION = "For employee E102, process a $750 expense.";

describe("workflow list", () => {
  test("shows each run with its employee, amount and status", async () => {
    mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(() => ({
        body: workflowList([
          workflowSummary(),
          workflowSummary({
            thread_id: "aaaaaaaa-0000-0000-0000-000000000000",
            employee_id: "E101",
            expense_amount: 6000,
            status: "failed",
            question: "For employee E101, process a $6000 expense.",
          }),
        ]),
      })),
    });

    renderApp("/workflows");

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("E102");
    expect(rows[0]).toHaveTextContent("$750");
    expect(rows[0]).toHaveTextContent("Awaiting approval");
    expect(rows[1]).toHaveTextContent("$6,000");
    expect(rows[1]).toHaveTextContent("Failed");

    expect(
      within(rows[0]!).getByRole("link", { name: /Open workflow 11111111/ }),
    ).toHaveAttribute("href", `/workflows/${THREAD}`);
  });

  test("shows an empty state", async () => {
    mockApi({ "GET /health": healthOk, "GET /workflows": workflowsRoute() });

    renderApp("/workflows");

    expect(await screen.findByText("No workflows yet. Start one above.")).toBeInTheDocument();
  });

  test("filters by status and says when nothing matches", async () => {
    const api = mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute((request) => ({
        body: workflowList(request.query.get("status") ? [] : [workflowSummary()]),
      })),
    });
    const user = userEvent.setup();

    renderApp("/workflows");

    await screen.findByRole("table");

    await user.selectOptions(screen.getByLabelText("Status"), "failed");

    expect(await screen.findByText('No workflows with status "Failed".')).toBeInTheDocument();

    const lastPageCall = api.to("GET", "/workflows").filter((c) => c.query.get("limit") === "20").at(-1);

    expect(lastPageCall?.query.get("status")).toBe("failed");
    expect(lastPageCall?.query.get("offset")).toBe("0");
  });

  test("pages through results", async () => {
    const api = mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute((request) => ({
        body: workflowList([workflowSummary()], {
          total: 45,
          offset: Number(request.query.get("offset") ?? 0),
        }),
      })),
    });
    const user = userEvent.setup();

    renderApp("/workflows");

    expect(await screen.findByText("1-20 of 45")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("21-40 of 45")).toBeInTheDocument();

    const offsets = api
      .to("GET", "/workflows")
      .filter((c) => c.query.get("limit") === "20")
      .map((c) => c.query.get("offset"));

    expect(offsets).toEqual(["0", "20"]);
  });

  test("starts from the page given in the address", async () => {
    const api = mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(() => ({ body: workflowList([workflowSummary()], { total: 45, offset: 20 }) })),
    });

    renderApp("/workflows?status=success&offset=20");

    await screen.findByRole("table");

    const call = api.to("GET", "/workflows").find((c) => c.query.get("limit") === "20");

    expect(call?.query.get("status")).toBe("success");
    expect(call?.query.get("offset")).toBe("20");
    expect(screen.getByLabelText("Status")).toHaveValue("success");
  });

  test("reports a failure and can retry", async () => {
    let attempts = 0;

    mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(() => {
        attempts += 1;

        return attempts === 1
          ? { status: 500, body: { detail: "Database is down." } }
          : { body: workflowList([workflowSummary()]) };
      }),
    });
    const user = userEvent.setup();

    renderApp("/workflows");

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("Database is down.");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("starting a workflow", () => {
  function routes(extra: Parameters<typeof mockApi>[0] = {}) {
    return mockApi({
      "GET /health": healthOk,
      "GET /workflows": workflowsRoute(),
      ...extra,
    });
  }

  test("sends the question and opens the new workflow", async () => {
    const api = routes({
      "POST /workflows": { status: 201, body: workflowResponse() },
      [`GET /workflows/${THREAD}`]: { body: workflowResponse() },
      [`GET /workflows/${THREAD}/audit`]: {
        body: { ...auditPage(), thread_id: THREAD },
      },
    });
    const user = userEvent.setup();

    renderApp("/workflows");

    await user.type(screen.getByLabelText("Question"), `  ${QUESTION}  `);
    await user.click(screen.getByRole("button", { name: "Start workflow" }));

    expect(await screen.findByRole("heading", { name: "Workflow" })).toBeInTheDocument();

    expect(api.to("POST", "/workflows")[0]?.body).toEqual({ question: QUESTION });

    // The question travels with the navigation so the detail page can show it.
    expect(await screen.findByText(QUESTION)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Approval needed" })).toBeInTheDocument();
  });

  test("cannot start an empty question", async () => {
    const api = routes();
    const user = userEvent.setup();

    renderApp("/workflows");

    const start = await screen.findByRole("button", { name: "Start workflow" });

    expect(start).toBeDisabled();

    await user.type(screen.getByLabelText("Question"), "   ");

    expect(start).toBeDisabled();
    expect(api.to("POST", "/workflows")).toHaveLength(0);
  });

  test("the example button fills in a sample question", async () => {
    routes();
    const user = userEvent.setup();

    renderApp("/workflows");

    await user.click(await screen.findByRole("button", { name: "Use example" }));

    expect(screen.getByLabelText("Question")).toHaveValue(
      "For employee E102, explain the expense reimbursement process for a $750 expense and state what approval is required.",
    );
    expect(screen.getByRole("button", { name: "Start workflow" })).toBeEnabled();
  });

  test("shows why a workflow could not be started and allows another try", async () => {
    routes({
      "POST /workflows": {
        status: 422,
        body: { detail: [{ loc: ["body", "question"], msg: "String should have at least 1 character" }] },
      },
    });
    const user = userEvent.setup();

    renderApp("/workflows");

    await user.type(await screen.findByLabelText("Question"), "x");
    await user.click(screen.getByRole("button", { name: "Start workflow" }));

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("The workflow could not be started");
    expect(alert).toHaveTextContent("question: String should have at least 1 character");

    await waitFor(() => expect(screen.getByRole("button", { name: "Start workflow" })).toBeEnabled());

    // Still on the list page, with the text kept.
    expect(screen.getByLabelText("Question")).toHaveValue("x");
  });
});
