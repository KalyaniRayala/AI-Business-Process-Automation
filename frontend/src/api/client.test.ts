import { describe, expect, test, vi } from "vitest";

import { mockApi } from "../test/mockApi";
import {
  ApiError,
  api,
  buildQuery,
  describeDetail,
  encodeNodeKey,
  request,
} from "./client";

describe("buildQuery", () => {
  test("is empty without parameters", () => {
    expect(buildQuery()).toBe("");
    expect(buildQuery({})).toBe("");
  });

  test("skips undefined, null and empty values but keeps zero and false", () => {
    expect(
      buildQuery({ a: undefined, b: null, c: "", d: 0, e: false, f: "x" }),
    ).toBe("?d=0&e=false&f=x");
  });

  test("repeats array parameters and encodes values", () => {
    expect(buildQuery({ edge_type: ["has_role", "member_of"], q: "a b&c" })).toBe(
      "?edge_type=has_role&edge_type=member_of&q=a+b%26c",
    );
  });
});

describe("describeDetail", () => {
  test("passes strings through", () => {
    expect(describeDetail("Workflow not found.")).toBe("Workflow not found.");
  });

  test("joins FastAPI validation problems with their field", () => {
    expect(
      describeDetail([
        { loc: ["query", "limit"], msg: "Input should be greater than or equal to 1" },
        { loc: ["body", "decision"], msg: "Field required" },
      ]),
    ).toBe("limit: Input should be greater than or equal to 1; decision: Field required");
  });

  test("handles problems without a location, and unknown shapes", () => {
    expect(describeDetail([{ msg: "bad" }])).toBe("bad");
    expect(describeDetail([])).toBeNull();
    expect(describeDetail({ nope: 1 })).toBeNull();
    expect(describeDetail(undefined)).toBeNull();
  });
});

describe("request", () => {
  test("returns the parsed JSON body of a successful GET", async () => {
    const mock = mockApi({ "GET /health": { body: { status: "ok" } } });

    await expect(request("/health")).resolves.toEqual({ status: "ok" });

    expect(mock.fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = mock.fetchMock.mock.calls[0]!;

    expect(url).toBe("/api/health");
    expect(init?.method).toBe("GET");
    // A GET has no body, so it must not claim a content type.
    expect(init?.headers).toEqual({ Accept: "application/json" });
  });

  test("sends JSON bodies with a content type", async () => {
    const mock = mockApi({ "POST /workflows": { status: 201, body: { thread_id: "t" } } });

    await request("/workflows", { method: "POST", body: { question: "hi" } });

    expect(mock.calls[0]?.body).toEqual({ question: "hi" });

    const init = mock.fetchMock.mock.calls[0]![1]!;

    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  test("turns an error with a string detail into an ApiError", async () => {
    mockApi({ "GET /workflows/x": { status: 404, body: { detail: "Workflow x was not found." } } });

    const failure = await request("/workflows/x").catch((e: unknown) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 404, message: "Workflow x was not found." });
  });

  test("describes validation errors (422)", async () => {
    mockApi({
      "GET /workflows": {
        status: 422,
        body: { detail: [{ loc: ["query", "limit"], msg: "Input should be <= 200" }] },
      },
    });

    await expect(request("/workflows")).rejects.toMatchObject({
      status: 422,
      message: "limit: Input should be <= 200",
    });
  });

  test("falls back to a generic message for non-JSON errors", async () => {
    mockApi({ "GET /x": { status: 502, text: "<html>Bad gateway</html>" } });

    await expect(request("/x")).rejects.toMatchObject({
      status: 502,
      message: "The server returned an error (HTTP 502).",
    });
  });

  test("reports an unreachable backend as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(request("/health")).rejects.toMatchObject({
      status: 0,
      message: "Cannot reach the API. Check that the backend is running and try again.",
    });
  });

  test("lets an aborted request surface as an AbortError, not an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")),
    );

    await expect(request("/health")).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("endpoint helpers", () => {
  test("encodeNodeKey keeps slashes and encodes everything else", () => {
    expect(encodeNodeKey("document:data/expense_process.txt")).toBe(
      "document%3Adata/expense_process.txt",
    );
    expect(encodeNodeKey("employee:E102")).toBe("employee%3AE102");
  });

  test("contextNode builds the path and repeated edge_type parameters", async () => {
    const mock = mockApi({
      "GET /context/nodes/document%3Adata/expense_process.txt": { body: {} },
    });

    await api.contextNode("document:data/expense_process.txt", {
      depth: 2,
      edge_type: ["evidenced_by", "part_of"],
    });

    const url = String(mock.fetchMock.mock.calls[0]![0]);

    expect(url).toBe(
      "/api/context/nodes/document%3Adata/expense_process.txt?depth=2&edge_type=evidenced_by&edge_type=part_of",
    );
  });

  test("listWorkflows omits unset filters", async () => {
    const mock = mockApi({ "GET /workflows": { body: { workflows: [], total: 0, limit: 20, offset: 0 } } });

    await api.listWorkflows({ status: undefined, limit: 20, offset: 0 });

    expect(String(mock.fetchMock.mock.calls[0]![0])).toBe("/api/workflows?limit=20&offset=0");
  });

  test("thread ids are encoded in paths", async () => {
    const mock = mockApi({ "POST /workflows/a%2Fb/decision": { body: {} } });

    await api.submitDecision("a/b", { decision: "approve" });

    expect(mock.calls[0]?.path).toBe("/workflows/a%2Fb/decision");
    expect(mock.calls[0]?.body).toEqual({ decision: "approve" });
  });
});
