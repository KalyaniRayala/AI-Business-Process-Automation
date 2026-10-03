"""
Dataset loading, and the shape of the datasets that ship with the
project. A malformed dataset must fail loudly rather than quietly
scoring fewer cases.
"""

import json

import pytest

from app.evals import datasets
from app.evals.datasets import DatasetError, load_dataset
from app.evals.suites import ALL_SUITES


@pytest.fixture
def dataset_dir(tmp_path, monkeypatch):
    """A temporary EVALS_DIR with a datasets/ folder."""

    monkeypatch.setenv("EVALS_DIR", str(tmp_path))

    directory = tmp_path / "datasets"
    directory.mkdir()

    return directory


def write(directory, name, payload):

    path = directory / f"{name}.json"

    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )

    return path


# ============================================================
# LOADING
# ============================================================

def test_loads_the_cases(dataset_dir):

    write(dataset_dir, "sample", {"cases": [{"id": "a"}, {"id": "b"}]})

    assert load_dataset("sample") == [{"id": "a"}, {"id": "b"}]


def test_evals_dir_follows_the_environment(tmp_path, monkeypatch):

    monkeypatch.setenv("EVALS_DIR", str(tmp_path))

    assert datasets.datasets_dir() == tmp_path / "datasets"


def test_evals_dir_defaults_inside_the_backend(monkeypatch):

    monkeypatch.delenv("EVALS_DIR", raising=False)

    assert datasets.evals_dir() == datasets.BACKEND_DIR / "evals"


def test_a_missing_dataset_names_the_file(dataset_dir):

    with pytest.raises(DatasetError, match="Dataset not found"):
        load_dataset("absent")


def test_invalid_json_is_reported(dataset_dir):

    write(dataset_dir, "broken", "{not json")

    with pytest.raises(DatasetError, match="not valid JSON"):
        load_dataset("broken")


def test_a_list_payload_is_rejected(dataset_dir):

    write(dataset_dir, "listy", "[]")

    with pytest.raises(DatasetError, match="must contain a JSON object"):
        load_dataset("listy")


@pytest.mark.parametrize("cases", [[], "nope", None])
def test_cases_must_be_a_non_empty_list(dataset_dir, cases):

    write(dataset_dir, "empty", {"cases": cases})

    with pytest.raises(DatasetError, match="non-empty 'cases' list"):
        load_dataset("empty")


def test_a_case_must_be_an_object(dataset_dir):

    write(dataset_dir, "odd", {"cases": ["just a string"]})

    with pytest.raises(DatasetError, match="must be an object"):
        load_dataset("odd")


@pytest.mark.parametrize("case_id", [None, "", "   ", 7])
def test_every_case_needs_an_id(dataset_dir, case_id):

    write(dataset_dir, "nameless", {"cases": [{"id": case_id}]})

    with pytest.raises(DatasetError, match="non-empty string 'id'"):
        load_dataset("nameless")


def test_duplicate_ids_are_rejected(dataset_dir):

    write(dataset_dir, "dupes", {"cases": [{"id": "a"}, {"id": "a"}]})

    with pytest.raises(DatasetError, match="duplicate case id"):
        load_dataset("dupes")


# ============================================================
# THE DATASETS THAT SHIP
# ============================================================

@pytest.mark.parametrize("suite", ALL_SUITES, ids=lambda suite: suite.name)
def test_shipped_dataset_loads(suite):

    cases = load_dataset(suite.dataset)

    assert cases, f"{suite.dataset} has no cases"


@pytest.mark.parametrize("suite", ALL_SUITES, ids=lambda suite: suite.name)
def test_shipped_dataset_has_enough_cases_to_mean_something(suite):

    assert len(load_dataset(suite.dataset)) >= 8


@pytest.mark.parametrize("suite", ALL_SUITES, ids=lambda suite: suite.name)
def test_a_known_gap_explains_itself(suite):

    for case in load_dataset(suite.dataset):

        if case.get("known_gap"):
            assert case.get("note"), (
                f"{suite.dataset}/{case['id']}: a known gap needs a note "
                "saying what is wrong"
            )
