import { Activity, Flame, Lightbulb, ListOrdered, Signal } from "lucide-react";
import { useState } from "react";

import { api, type WindowParams } from "../api/client";
import { PRIORITIES, type Priority, type Recommendation } from "../api/types";
import { WindowFilter } from "../components/WindowFilter";
import {
  Badge,
  Bar,
  EmptyState,
  ErrorMessage,
  Loading,
  PageHeader,
  PriorityBadge,
  Section,
  Stat,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { humanize } from "../utils/format";

const TYPE_LABELS: Record<Recommendation["type"], string> = {
  automate_manual_step: "Automate a manual step",
  complete_partial_automation: "Complete partial automation",
  reduce_approval_wait: "Reduce approval wait",
  reduce_step_failures: "Reduce step failures",
};

const SCORE_PARTS = [
  ["impact", "Impact"],
  ["feasibility", "Feasibility"],
  ["confidence", "Confidence"],
] as const;

function RecommendationCard({ item }: { item: Recommendation }) {
  return (
    <li className="reco" id={item.recommendation_id}>
      <header className="reco__head">
        <div>
          <p className="reco__type">{TYPE_LABELS[item.type]}</p>
          <h3>{item.title}</h3>
        </div>

        <div className="reco__score" aria-label={`Score ${item.score} out of 100`}>
          <strong>{item.score}</strong>
          <small>/ 100</small>
        </div>
      </header>

      <p>{item.summary}</p>

      <div className="chips">
        <PriorityBadge priority={item.priority} />
        <Badge>{humanize(item.effort)} effort</Badge>
        <Badge>{humanize(item.confidence)} confidence</Badge>
        {item.requires_human_decision && <Badge tone="warning">Needs a business decision</Badge>}
      </div>

      <dl className="score-parts">
        {SCORE_PARTS.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>
              <Bar value={item.scores[key]} label={`${label} ${item.scores[key]}`} />
              <span>{item.scores[key].toFixed(2)}</span>
            </dd>
          </div>
        ))}
      </dl>

      <div className="two-column">
        <div>
          <h4>Why</h4>
          <ul>
            {item.rationale.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>

        <div>
          <h4>Suggested actions</h4>
          <ul>
            {item.suggested_actions.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      </div>

      {item.evidence.length > 0 && (
        <p className="muted">
          <small>Source: {item.evidence.join(", ")}</small>
        </p>
      )}
    </li>
  );
}

export function RecommendationsPage() {
  const [range, setRange] = useState<WindowParams>({});
  const [minPriority, setMinPriority] = useState<Priority>("low");

  const result = useAsync(
    (signal) => api.recommendations({ ...range, min_priority: minPriority }, signal),
    [range, minPriority],
  );

  const data = result.data;

  return (
    <>
      <PageHeader
        title="Automation recommendations"
        subtitle="What to automate or improve next, ranked and explained from how the process actually runs."
      />

      <WindowFilter onApply={setRange}>
        <label>
          Minimum priority
          <select
            value={minPriority}
            onChange={(e) => setMinPriority(e.target.value as Priority)}
          >
            {PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>
                {humanize(priority)}
              </option>
            ))}
          </select>
        </label>
      </WindowFilter>

      {result.error && <ErrorMessage error={result.error} onRetry={result.reload} />}
      {!data && result.loading && <Loading />}

      {data && (
        <>
          {data.warnings.map((warning) => (
            <div key={warning} className="alert alert--warning" role="status">
              {warning}
            </div>
          ))}

          <dl className="stats">
            <Stat label="Recommendations" icon={Lightbulb} value={data.summary.total} />
            <Stat label="High priority" icon={Flame} value={data.summary.by_priority["high"] ?? 0} tone="danger" />
            <Stat label="Medium priority" icon={Signal} value={data.summary.by_priority["medium"] ?? 0} tone="warning" />
            <Stat label="Low priority" icon={ListOrdered} value={data.summary.by_priority["low"] ?? 0} />
            <Stat label="Runs analysed" icon={Activity} value={data.basis.window.workflows_analyzed} />
          </dl>

          <Section title="Ranked recommendations">
            {data.recommendations.length === 0 ? (
              <EmptyState>No recommendations at this priority.</EmptyState>
            ) : (
              <ol className="reco-cards">
                {data.recommendations.map((item) => (
                  <RecommendationCard key={item.recommendation_id} item={item} />
                ))}
              </ol>
            )}
          </Section>

          <Section title="How scores are calculated">
            <details>
              <summary>Scoring rubric</summary>
              <p>
                <code>{data.method.formula}</code>
              </p>
              <ul>
                {data.method.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
              <p className="muted">
                High priority from {data.method.priority_thresholds["high"]}, medium from{" "}
                {data.method.priority_thresholds["medium"]}.
              </p>
            </details>
          </Section>
        </>
      )}
    </>
  );
}
