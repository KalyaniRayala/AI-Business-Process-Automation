import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { useAsync } from "./useAsync";

/** A promise the test resolves or rejects by hand. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;

  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });

  return { promise, resolve, reject };
}

describe("useAsync", () => {
  test("starts loading, then provides the data", async () => {
    const { result } = renderHook(() => useAsync(async () => "hello", []));

    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeUndefined();

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toBe("hello");
    expect(result.current.error).toBeUndefined();
  });

  test("provides the error when loading fails", async () => {
    const { result } = renderHook(() =>
      useAsync(async () => {
        throw new Error("boom");
      }, []),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error?.message).toBe("boom");
    expect(result.current.data).toBeUndefined();
  });

  test("wraps a thrown non-error value", async () => {
    const { result } = renderHook(() =>
      useAsync(() => Promise.reject("just a string"), []),
    );

    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));

    expect(result.current.error?.message).toBe("just a string");
  });

  test("reload runs again, keeping the old data meanwhile", async () => {
    const second = deferred<string>();
    const load = vi
      .fn<(signal: AbortSignal) => Promise<string>>()
      .mockResolvedValueOnce("first")
      .mockReturnValueOnce(second.promise);

    const { result } = renderHook(() => useAsync(load, []));

    await waitFor(() => expect(result.current.data).toBe("first"));

    act(() => result.current.reload());

    await waitFor(() => expect(result.current.loading).toBe(true));

    // The previous result stays visible while the new request is in flight.
    expect(result.current.data).toBe("first");

    await act(async () => second.resolve("second"));

    await waitFor(() => expect(result.current.data).toBe("second"));

    expect(result.current.loading).toBe(false);
    expect(load).toHaveBeenCalledTimes(2);
  });

  test("a successful reload clears an earlier error", async () => {
    const load = vi
      .fn<(signal: AbortSignal) => Promise<string>>()
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValueOnce("back");

    const { result } = renderHook(() => useAsync(load, []));

    await waitFor(() => expect(result.current.error?.message).toBe("down"));

    act(() => result.current.reload());

    await waitFor(() => expect(result.current.data).toBe("back"));

    expect(result.current.error).toBeUndefined();
  });

  test("loads again when the dependencies change", async () => {
    const load = vi.fn(async () => "data");

    const { result, rerender } = renderHook(({ id }) => useAsync(load, [id]), {
      initialProps: { id: 1 },
    });

    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ id: 1 });

    expect(load).toHaveBeenCalledTimes(1);

    rerender({ id: 2 });

    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  test("ignores a response that arrives after a newer request started", async () => {
    const slow = deferred<string>();
    const fast = deferred<string>();

    const { result, rerender } = renderHook(
      ({ id }) => useAsync(() => (id === 1 ? slow.promise : fast.promise), [id]),
      { initialProps: { id: 1 } },
    );

    // Move on before the first request has answered.
    rerender({ id: 2 });

    await act(async () => fast.resolve("new"));
    await waitFor(() => expect(result.current.data).toBe("new"));

    // The slow, outdated answer must not overwrite it.
    await act(async () => slow.resolve("stale"));

    expect(result.current.data).toBe("new");
    expect(result.current.loading).toBe(false);
  });

  test("an outdated failure does not replace newer data", async () => {
    const slow = deferred<string>();
    const fast = deferred<string>();

    const { result, rerender } = renderHook(
      ({ id }) => useAsync(() => (id === 1 ? slow.promise : fast.promise), [id]),
      { initialProps: { id: 1 } },
    );

    rerender({ id: 2 });

    await act(async () => fast.resolve("new"));
    await waitFor(() => expect(result.current.data).toBe("new"));

    await act(async () => slow.reject(new Error("late failure")));

    expect(result.current.error).toBeUndefined();
    expect(result.current.data).toBe("new");
  });

  test("aborts the request when the component goes away", async () => {
    const pending = deferred<string>();
    let seen: AbortSignal | undefined;

    const { unmount } = renderHook(() =>
      useAsync((signal) => {
        seen = signal;
        return pending.promise;
      }, []),
    );

    expect(seen?.aborted).toBe(false);

    unmount();

    expect(seen?.aborted).toBe(true);
  });

  test("aborts the previous request when the dependencies change", async () => {
    const signals: AbortSignal[] = [];

    const { rerender } = renderHook(
      ({ id }) =>
        useAsync((signal) => {
          signals.push(signal);
          return new Promise<string>(() => undefined);
        }, [id]),
      { initialProps: { id: 1 } },
    );

    rerender({ id: 2 });

    expect(signals).toHaveLength(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });
});
