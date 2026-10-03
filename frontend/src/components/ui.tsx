import { Inbox, type LucideIcon } from "lucide-react";
import { useId, type ReactNode } from "react";

import { ApiError } from "../api/client";
import type { Priority } from "../api/types";
import { humanize } from "../utils/format";

export type Tone = "success" | "danger" | "warning" | "info" | "neutral";

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

const STATUS_TONES: Record<string, Tone> = {
  success: "success",
  completed: "success",
  approved: "success",
  not_required: "neutral",
  running: "info",
  in_progress: "info",
  info: "info",
  awaiting_approval: "warning",
  pending: "warning",
  rejected: "danger",
  failed: "danger",
  failure: "danger",
  error: "danger",
};

/** Workflow, outcome, approval and audit statuses share one colour scheme. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge>-</Badge>;

  return <Badge tone={STATUS_TONES[status] ?? "neutral"}>{humanize(status)}</Badge>;
}

const PRIORITY_TONES: Record<Priority, Tone> = {
  high: "danger",
  medium: "warning",
  low: "neutral",
};

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <Badge tone={PRIORITY_TONES[priority]}>{humanize(priority)} priority</Badge>;
}

const AUTOMATION_TONES: Record<string, Tone> = {
  automated: "success",
  partial: "warning",
  manual: "danger",
};

export function AutomationBadge({ level }: { level: string }) {
  return <Badge tone={AUTOMATION_TONES[level] ?? "neutral"}>{humanize(level)}</Badge>;
}

export function Loading({ label = "Loading..." }: { label?: string }) {
  return (
    <p className="loading" role="status">
      {label}
    </p>
  );
}

export function ErrorMessage({
  error,
  onRetry,
  title = "Something went wrong",
}: {
  error: Error;
  onRetry?: () => void;
  title?: string;
}) {
  const unreachable = error instanceof ApiError && error.status === 0;

  return (
    <div className="alert alert--danger" role="alert">
      <strong>{unreachable ? "Backend unreachable" : title}</strong>
      <p>{error.message}</p>
      {onRetry && (
        <button type="button" className="button button--small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <Inbox aria-hidden="true" />
      <span className="empty__text">{children}</span>
    </div>
  );
}

/** A horizontal bar for a value between 0 and 1. */
export function Bar({
  value,
  label,
  tone = "info",
}: {
  value: number;
  label: string;
  tone?: Tone;
}) {
  const percent = Math.max(0, Math.min(1, value)) * 100;

  return (
    <div
      className="bar"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
    >
      <div className={`bar__fill bar__fill--${tone}`} style={{ width: `${percent}%` }} />
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
  icon: Icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  /** Decorative: the label already says what the number is. */
  icon?: LucideIcon;
}) {
  return (
    <div className={`stat${tone ? ` stat--${tone}` : ""}`}>
      <dt className="stat__label">
        {Icon && (
          <span className="stat__icon" aria-hidden="true">
            <Icon />
          </span>
        )}
        {label}
      </dt>
      <dd className="stat__value">{value}</dd>
      {hint && <dd className="stat__hint">{hint}</dd>}
    </div>
  );
}

/** A titled region; the heading names it for screen readers. */
export function Section({
  title,
  children,
  actions,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  const headingId = useId();

  return (
    <section className="section" aria-labelledby={headingId}>
      <div className="section__header">
        <h2 id={headingId}>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>
      {actions}
    </header>
  );
}

export function Pagination({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;

  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);

  return (
    <nav className="pagination" aria-label="Pagination">
      <button
        type="button"
        className="button button--small"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Previous
      </button>
      <span>
        {first}-{last} of {total}
      </span>
      <button
        type="button"
        className="button button--small"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Next
      </button>
    </nav>
  );
}
