"""
Dataset loading.

A dataset is a JSON file in `backend/evals/datasets/`:

    {
      "suite": "extraction",
      "description": "...",
      "cases": [ {"id": "...", ...}, ... ]
    }

Case fields beyond `id` are the suite's business. Common optional
fields handled by the runner are `known_gap` (a documented weakness,
reported but excluded from the gate) and `note`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# backend/app/evals/datasets.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


def evals_dir() -> Path:
    """
    Root of the eval data. `EVALS_DIR` overrides it so tests can use
    a temporary directory.
    """

    configured = os.getenv("EVALS_DIR")

    if configured:
        return Path(configured)

    return BACKEND_DIR / "evals"


def datasets_dir() -> Path:

    return evals_dir() / "datasets"


def dataset_path(name: str) -> Path:

    return datasets_dir() / f"{name}.json"


class DatasetError(Exception):
    """A dataset is missing, unreadable or malformed."""


def load_dataset(name: str) -> list[dict]:
    """
    Cases from the named dataset, validated. Raises DatasetError
    with a message that names the file, so a typo in a dataset is
    obvious instead of surfacing as a confusing failure later.
    """

    path = dataset_path(name)

    if not path.is_file():
        raise DatasetError(f"Dataset not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))

    except json.JSONDecodeError as error:
        raise DatasetError(f"{path} is not valid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise DatasetError(f"{path} must contain a JSON object.")

    cases = payload.get("cases")

    if not isinstance(cases, list) or not cases:
        raise DatasetError(f"{path} must contain a non-empty 'cases' list.")

    seen: set[str] = set()

    for position, case in enumerate(cases):

        if not isinstance(case, dict):
            raise DatasetError(
                f"{path}: case {position} must be an object."
            )

        case_id = case.get("id")

        if not isinstance(case_id, str) or not case_id.strip():
            raise DatasetError(
                f"{path}: case {position} needs a non-empty string 'id'."
            )

        if case_id in seen:
            raise DatasetError(f"{path}: duplicate case id {case_id!r}.")

        seen.add(case_id)

    return cases
