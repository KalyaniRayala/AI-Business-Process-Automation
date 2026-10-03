# Evals

Quality measurement for the parts of the platform that the test
suite cannot pin down.

`pytest` stubs the LLM and the retriever, so it proves the workflow
is wired correctly but says nothing about whether the right chunk
comes back or whether the answer is grounded. The evals score that
against labelled datasets, and fail the run when a score drops.

- `app/evals/` — the harness (scorers, runner, report, suites).
- `evals/datasets/` — the labelled cases, one JSON file per suite.
- `evals/cassettes/` — recorded Cohere responses, so the model
  suites can run with no key and no cost.

## Run them

From `backend/`:

```bash
python -m app.evals list                     # suites and case counts
python -m app.evals run                      # deterministic suites
python -m app.evals run --suite policy       # one suite (repeatable)
python -m app.evals run --replay             # add the model suites, from cassettes
python -m app.evals run --live               # add them, calling Cohere
python -m app.evals run --live --record      # ...and save the responses
python -m app.evals run --json ../report.json --markdown ../report.md
```

The process exits `1` when the gate fails, so CI can call it
directly. `--quiet` prints nothing and leaves only the exit code.

## The suites

| Suite | Measures | Needs |
|---|---|---|
| `extraction` | Employee id and amount parsed from the question | nothing |
| `policy` | Approval thresholds and the gate they drive | nothing |
| `routing` | Recovery routing, and the path after a decision | the workflow graph |
| `retrieval` | Recall of the policy sentence an answer needs | Cohere + ingested documents |
| `grounding` | Answers stay in the context and refuse when it is silent | Cohere |

The deterministic suites run anywhere. `routing` imports the
workflow graph, which opens the configured checkpointer: point
`DATABASE_URL` at SQLite to run it without PostgreSQL.

The model suites are **skipped** in the default offline mode, and a
skip is not a failure: a missing key must not look like a quality
regression. They produce a score only with `--replay` or `--live`.

## Scoring

Every scorer is a substring, numeric or set comparison. No model
judges another model, so a score means the same thing on every run
and a failure can be read without rerunning anything.

A case scores 0..1 and only a perfect score counts as a pass. A
suite passes when the mean score of its scored cases reaches its
threshold (set on the `Suite`, in `app/evals/suites/`). The
deterministic suites are held at 100%. `grounding` is held at 90%,
because generation varies and one partially missed phrase should be
visible in the report without failing the build — a hallucination or
a wrong refusal still fails it, since those score 0.

## Known gaps

A case marked `"known_gap": true` records behaviour that is wrong
today. It is scored and reported, but kept out of the gate, so a
weakness can be written down without blocking every run. Each one
carries a `note` explaining the problem.

Current gaps, all in question parsing:

- any bare number is read as the amount, so `"a claim from 2024 for
  300 dollars"` applies the rule for $2,024,
- amounts written in words are not parsed at all,
- a zero amount looks like a missing amount,
- a tool result that is not a dictionary is not treated as a
  failure, so a malformed lookup goes on to approval instead of
  recovery.

A test fails if a case marked as a gap starts passing, so fixing one
forces the marker to be removed.

## Cassettes

A cassette is a JSON file of recorded Cohere responses, keyed by a
hash of the request. Changing a prompt, a question or a query
invalidates its recording rather than silently replaying an answer
to a different question; the case is then skipped and says what to
re-record.

```bash
python -m app.evals run --live --record     # writes evals/cassettes/default.json
python -m app.evals run --replay            # free, offline, deterministic
```

`--live` spends real Cohere quota, and `retrieval` reads whatever is
in the Chroma store, so record from an environment whose documents
are the ones you mean to measure.

## Adding a case

Add an object to the suite's dataset file with a unique `id`. The
loader rejects a missing or duplicated id, and the tests check that
each dataset still has enough cases to mean something and that every
known gap explains itself.
