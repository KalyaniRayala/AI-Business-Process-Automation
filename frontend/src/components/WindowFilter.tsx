import { useState, type FormEvent, type ReactNode } from "react";

import type { WindowParams } from "../api/client";
import { localInputToIso } from "../utils/format";

const MAX_LIMIT = 5000;

/** Limit and time range for discovery and recommendations. */
export function WindowFilter({
  onApply,
  children,
}: {
  onApply: (params: WindowParams) => void;
  children?: ReactNode;
}) {
  const [limit, setLimit] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const sinceIso = localInputToIso(since);
  const untilIso = localInputToIso(until);
  const invalidRange = Boolean(sinceIso && untilIso && untilIso < sinceIso);

  function submit(event: FormEvent) {
    event.preventDefault();

    if (invalidRange) return;

    onApply({
      limit: limit ? Math.min(MAX_LIMIT, Math.max(1, Number(limit))) : undefined,
      since: sinceIso,
      until: untilIso,
    });
  }

  return (
    <form className="filters" onSubmit={submit} aria-label="Analysis window">
      <label>
        Most recent runs
        <input
          type="number"
          min={1}
          max={MAX_LIMIT}
          placeholder="1000"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
        />
      </label>

      <label>
        Started after
        <input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
      </label>

      <label>
        Started before
        <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
      </label>

      {children}

      <button type="submit" className="button button--primary" disabled={invalidRange}>
        Apply
      </button>

      {invalidRange && (
        <p className="field-error" role="alert">
          The end of the range is before its start.
        </p>
      )}
    </form>
  );
}
