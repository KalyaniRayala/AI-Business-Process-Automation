"""
Scoring functions.

Every scorer is pure, deterministic and returns a `Score` in 0..1 with
a short explanation. No scorer uses an LLM to judge an LLM: the
checks here are substring, numeric and set comparisons, so a result
means the same thing on every run.
"""

from __future__ import annotations

from app.evals.types import Score


# The exact sentence llm_service tells the model to use when the
# context does not answer the question.
REFUSAL_SENTENCE = (
    "I could not find this information in the provided documents."
)

# Shorter phrasings that still count as a refusal, since the model
# does not always reproduce the sentence word for word.
REFUSAL_MARKERS = (
    "could not find this information",
    "couldn't find this information",
    "not find this information",
    "is not in the provided documents",
    "is not provided in the documents",
)


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace for forgiving comparisons."""

    return " ".join(str(text).split()).lower()


# ============================================================
# VALUE SCORERS
# ============================================================

def exact_match(actual, expected, label: str = "value") -> Score:
    """1.0 when the values are equal, including when both are None."""

    if actual == expected:
        return Score(1.0)

    return Score(
        0.0,
        f"{label}: expected {expected!r}, got {actual!r}"
    )


def text_match(actual, expected, label: str = "text") -> Score:
    """Equality ignoring case and surrounding whitespace."""

    if actual is None or expected is None:
        return exact_match(actual, expected, label)

    if normalize(actual) == normalize(expected):
        return Score(1.0)

    return Score(
        0.0,
        f"{label}: expected {expected!r}, got {actual!r}"
    )


def numeric_match(
    actual: float | None,
    expected: float | None,
    tolerance: float = 1e-9,
    label: str = "amount"
) -> Score:
    """Equality for numbers, within `tolerance`. None must match None."""

    if actual is None or expected is None:
        return exact_match(actual, expected, label)

    if abs(float(actual) - float(expected)) <= tolerance:
        return Score(1.0)

    return Score(
        0.0,
        f"{label}: expected {expected}, got {actual}"
    )


def set_match(
    actual,
    expected,
    label: str = "items"
) -> Score:
    """
    Compares two collections as sets, ignoring case and order.
    Partial credit is the Jaccard overlap, so a near-miss scores
    higher than an unrelated answer.
    """

    actual_set = {normalize(item) for item in (actual or [])}
    expected_set = {normalize(item) for item in (expected or [])}

    if actual_set == expected_set:
        return Score(1.0)

    union = actual_set | expected_set

    # Both empty is handled by the equality check above.
    overlap = len(actual_set & expected_set) / len(union)

    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)

    parts = []

    if missing:
        parts.append(f"missing {missing}")

    if extra:
        parts.append(f"unexpected {extra}")

    return Score(overlap, f"{label}: " + ", ".join(parts))


# ============================================================
# TEXT SCORERS
# ============================================================

def keyword_coverage(
    text: str,
    required: list[str] | None = None,
    label: str = "answer"
) -> Score:
    """
    Fraction of the required phrases present in `text`
    (case-insensitive substring). No requirements scores 1.0.
    """

    required = required or []

    if not required:
        return Score(1.0)

    haystack = normalize(text)

    missing = [
        phrase
        for phrase in required
        if normalize(phrase) not in haystack
    ]

    value = (len(required) - len(missing)) / len(required)

    if not missing:
        return Score(1.0)

    return Score(value, f"{label} is missing {missing}")


def forbidden_absent(
    text: str,
    forbidden: list[str] | None = None,
    label: str = "answer"
) -> Score:
    """
    1.0 when none of the forbidden phrases appear. Used to catch
    invented facts: a phrase that is not in the context must not be
    in the answer.
    """

    forbidden = forbidden or []

    if not forbidden:
        return Score(1.0)

    haystack = normalize(text)

    present = [
        phrase
        for phrase in forbidden
        if normalize(phrase) in haystack
    ]

    if not present:
        return Score(1.0)

    value = (len(forbidden) - len(present)) / len(forbidden)

    return Score(value, f"{label} contains forbidden {present}")


def is_refusal(text: str) -> bool:
    """Whether the answer declines to answer from the context."""

    haystack = normalize(text)

    return any(marker in haystack for marker in REFUSAL_MARKERS)


def refusal_match(text: str, expected: bool) -> Score:
    """
    Scores the refusal behaviour: the model must refuse when the
    context cannot answer, and must not refuse when it can.
    """

    refused = is_refusal(text)

    if refused == expected:
        return Score(1.0)

    if expected:
        return Score(0.0, "expected a refusal, got an answer")

    return Score(0.0, "refused although the context answers the question")


# ============================================================
# RETRIEVAL SCORERS
# ============================================================

def snippet_recall(
    chunks: list[str],
    expected_snippets: list[str] | None = None,
    label: str = "retrieval"
) -> Score:
    """
    Fraction of the expected snippets found somewhere in the
    retrieved chunks: recall@k, where k is the number of chunks.
    """

    expected_snippets = expected_snippets or []

    if not expected_snippets:
        return Score(1.0)

    haystack = normalize(" \n ".join(chunks))

    missing = [
        snippet
        for snippet in expected_snippets
        if normalize(snippet) not in haystack
    ]

    value = (
        len(expected_snippets) - len(missing)
    ) / len(expected_snippets)

    if not missing:
        return Score(1.0)

    return Score(value, f"{label} did not return {missing}")


def rank_of_first_hit(
    chunks: list[str],
    expected_snippets: list[str] | None = None
) -> int | None:
    """
    1-based position of the first chunk containing any expected
    snippet, or None when no chunk does. Reported as a metric; it
    does not affect the score.
    """

    for position, chunk in enumerate(chunks, start=1):

        normalized = normalize(chunk)

        for snippet in expected_snippets or []:

            if normalize(snippet) in normalized:
                return position

    return None
