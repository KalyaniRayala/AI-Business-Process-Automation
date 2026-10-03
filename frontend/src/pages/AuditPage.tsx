import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { AUDIT_EVENT_TYPES, type AuditEvent } from "../api/types";
import {
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
  Section,
  StatusBadge,
} from "../components/ui";
import { formatDateTime, humanize, shortId } from "../utils/format";

const PAGE_SIZE = 100;

interface Filters {
  thread_id?: string;
  event_type?: string;
  entity_type?: string;
  entity_id?: string;
}

/**
 * The audit API returns events oldest first, so "load more" continues
 * forward from the last event shown (after_id).
 */
function useAuditFeed(filters: Filters) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [nextAfterId, setNextAfterId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | undefined>(undefined);

  // Ignore a response if the filters changed (or a newer request began).
  const requestId = useRef(0);

  const load = useCallback(
    async (afterId: number | undefined, reset: boolean) => {
      const mine = ++requestId.current;

      setLoading(true);
      setError(undefined);

      try {
        const page = await api.searchAudit({ ...filters, limit: PAGE_SIZE, after_id: afterId });

        if (mine !== requestId.current) return;

        setEvents((current) => (reset ? page.events : [...current, ...page.events]));
        setNextAfterId(page.next_after_id);
      } catch (failure) {
        if (mine !== requestId.current) return;

        setError(failure instanceof Error ? failure : new Error(String(failure)));
      } finally {
        if (mine === requestId.current) setLoading(false);
      }
    },
    [filters],
  );

  useEffect(() => {
    setEvents([]);
    setNextAfterId(null);
    void load(undefined, true);
  }, [load]);

  return {
    events,
    loading,
    error,
    hasMore: nextAfterId !== null,
    loadMore: () => (nextAfterId !== null ? load(nextAfterId, false) : undefined),
    reload: () => load(undefined, true),
  };
}

function FilterForm({ onApply }: { onApply: (filters: Filters) => void }) {
  const [threadId, setThreadId] = useState("");
  const [eventType, setEventType] = useState("");
  const [entityType, setEntityType] = useState("");
  const [entityId, setEntityId] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();

    onApply({
      thread_id: threadId.trim() || undefined,
      event_type: eventType || undefined,
      entity_type: entityType.trim() || undefined,
      entity_id: entityId.trim() || undefined,
    });
  }

  function clear() {
    setThreadId("");
    setEventType("");
    setEntityType("");
    setEntityId("");
    onApply({});
  }

  return (
    <form className="filters" onSubmit={submit} aria-label="Audit filters">
      <label>
        Workflow ID
        <input
          type="text"
          value={threadId}
          maxLength={64}
          onChange={(e) => setThreadId(e.target.value)}
        />
      </label>

      <label>
        Event type
        <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">Any</option>
          {AUDIT_EVENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {humanize(type)}
            </option>
          ))}
        </select>
      </label>

      <label>
        Entity type
        <input
          type="text"
          value={entityType}
          maxLength={50}
          placeholder="employee"
          onChange={(e) => setEntityType(e.target.value)}
        />
      </label>

      <label>
        Entity ID
        <input
          type="text"
          value={entityId}
          maxLength={100}
          placeholder="E102"
          onChange={(e) => setEntityId(e.target.value)}
        />
      </label>

      <button type="submit" className="button button--primary">
        Search
      </button>
      <button type="button" className="button" onClick={clear}>
        Clear
      </button>
    </form>
  );
}

export function AuditPage() {
  const [filters, setFilters] = useState<Filters>({});
  const feed = useAuditFeed(filters);

  return (
    <>
      <PageHeader
        title="Audit trail"
        subtitle="An append-only record of everything the platform did, oldest first."
      />

      <FilterForm onApply={setFilters} />

      <Section title="Events">
        {feed.error && <ErrorMessage error={feed.error} onRetry={feed.reload} />}

        {feed.events.length === 0 && !feed.loading && !feed.error && (
          <EmptyState>No audit events match.</EmptyState>
        )}

        {feed.events.length > 0 && (
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Audit events</caption>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Node</th>
                  <th>Status</th>
                  <th>Workflow</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {feed.events.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                    </td>
                    <td>{humanize(event.event_type)}</td>
                    <td>{event.node ?? "-"}</td>
                    <td>
                      <StatusBadge status={event.status} />
                    </td>
                    <td>
                      {event.thread_id ? (
                        <Link to={`/workflows/${event.thread_id}`}>{shortId(event.thread_id)}</Link>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td className="truncate" title={event.error_message ?? event.message}>
                      {event.error_message ?? event.message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {feed.loading && <Loading />}

        {feed.hasMore && !feed.loading && (
          <button type="button" className="button" onClick={() => void feed.loadMore()}>
            Load more
          </button>
        )}
      </Section>
    </>
  );
}
