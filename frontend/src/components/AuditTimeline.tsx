import type { AuditEvent } from "../api/types";
import { formatDateTime, humanize } from "../utils/format";
import { StatusBadge } from "./ui";

/** Audit events, oldest first, with the raw details one click away. */
export function AuditTimeline({ events }: { events: AuditEvent[] }) {
  return (
    <ol className="timeline">
      {events.map((event) => (
        <li key={event.id} className={`timeline__item timeline__item--${event.status}`}>
          <div className="timeline__head">
            <strong>{humanize(event.event_type)}</strong>
            {event.node && <span className="muted">{event.node}</span>}
            <StatusBadge status={event.status} />
            <time dateTime={event.occurred_at} className="muted">
              {formatDateTime(event.occurred_at)}
            </time>
          </div>

          <p>{event.message}</p>

          {(event.actor_id || event.actor_type !== "system") && (
            <p className="muted">
              <small>
                By {event.actor_type}
                {event.actor_id ? `: ${event.actor_id}` : ""}
              </small>
            </p>
          )}

          {event.error_message && (
            <p className="timeline__error">
              {event.error_type ? `${event.error_type}: ` : ""}
              {event.error_message}
            </p>
          )}

          {event.details && Object.keys(event.details).length > 0 && (
            <details>
              <summary>Details</summary>
              <pre>{JSON.stringify(event.details, null, 2)}</pre>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}
