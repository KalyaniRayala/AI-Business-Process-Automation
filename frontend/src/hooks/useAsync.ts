import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";

export interface AsyncState<T> {
  data: T | undefined;
  error: ApiError | Error | undefined;
  loading: boolean;
  reload: () => void;
}

/**
 * Runs `load` on mount and whenever `deps` change (and on reload()).
 * The previous data stays visible while a new request is in flight, and a
 * response that arrives after a newer request started is ignored.
 */
export function useAsync<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | Error | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  // Always call the latest `load` without making it a dependency.
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();

    setLoading(true);

    loadRef
      .current(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setError(undefined);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setError(failure instanceof Error ? failure : new Error(String(failure)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  return { data, error, loading, reload };
}
