"""
Recorded model responses.

Suites never call Cohere directly: they go through a gateway. A live
gateway calls the real services (and can record what came back); a
replay gateway answers from a cassette file, so the model suites can
run in CI with no key, no network and no cost.

Recordings are keyed by a hash of the request, so changing a prompt
or a query invalidates its recording instead of silently replaying
an answer to a different question.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class MissingRecording(Exception):
    """A replayed request has no recording in the cassette."""


def request_key(kind: str, *parts: object) -> str:
    """Stable key for a request: its kind plus a hash of its inputs."""

    digest = hashlib.sha256()

    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x00")

    return f"{kind}:{digest.hexdigest()[:16]}"


# ============================================================
# CASSETTE FILE
# ============================================================

@dataclass
class Cassette:
    """
    A JSON file of recorded responses. `summary` fields are only
    there to make the file readable in review; the key is what the
    lookup uses.
    """

    path: Path
    entries: dict[str, dict] = field(default_factory=dict)
    dirty: bool = False

    @classmethod
    def load(cls, path: Path | str) -> "Cassette":

        path = Path(path)

        if not path.is_file():
            return cls(path=path)

        payload = json.loads(path.read_text(encoding="utf-8"))

        return cls(path=path, entries=payload.get("entries", {}))

    def get(self, key: str):

        if key not in self.entries:
            raise MissingRecording(
                f"No recording for {key} in {self.path.name}. "
                "Record it with: python -m app.evals run --live --record"
            )

        return self.entries[key]["response"]

    def put(self, key: str, response, summary: str = "") -> None:

        self.entries[key] = {"summary": summary, "response": response}
        self.dirty = True

    def save(self) -> None:

        self.path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "note": (
                "Recorded Cohere responses for the eval suites. "
                "Regenerate with: python -m app.evals run --live --record"
            ),
            "entries": dict(sorted(self.entries.items())),
        }

        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        self.dirty = False


# ============================================================
# GATEWAYS
# ============================================================

class ModelGateway(Protocol):
    """The only model access a suite is allowed."""

    def answer(self, question: str, context: str) -> str:
        ...

    def retrieve(self, query: str, top_k: int) -> list[str]:
        ...


def _shorten(text: str, limit: int = 80) -> str:

    collapsed = " ".join(str(text).split())

    if len(collapsed) <= limit:
        return collapsed

    return collapsed[: limit - 3] + "..."


@dataclass
class ReplayGateway:
    """Answers only from a cassette."""

    cassette: Cassette

    def answer(self, question: str, context: str) -> str:

        return self.cassette.get(request_key("answer", question, context))

    def retrieve(self, query: str, top_k: int) -> list[str]:

        return self.cassette.get(request_key("retrieval", query, top_k))


@dataclass
class LiveGateway:
    """
    Calls the real services. When `cassette` is set, every response
    is recorded so the same run can be replayed later.
    """

    cassette: Cassette | None = None

    def answer(self, question: str, context: str) -> str:

        # Imported lazily: these modules require a Cohere key at
        # import time, which offline runs must not need.
        from app.services.llm_service import generate_answer

        response = generate_answer(question=question, context=context)

        if self.cassette is not None:
            self.cassette.put(
                request_key("answer", question, context),
                response,
                summary=_shorten(question),
            )

        return response

    def retrieve(self, query: str, top_k: int) -> list[str]:

        from app.services.rag_service import retrieve_context

        chunks = list(retrieve_context(query, top_k=top_k))

        if self.cassette is not None:
            self.cassette.put(
                request_key("retrieval", query, top_k),
                chunks,
                summary=_shorten(query),
            )

        return chunks
