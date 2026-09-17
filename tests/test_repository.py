"""Tests for artifact loading and the repository accessors.

Case IDs refer to docs/edge-case.md. These use a temporary artifact rather than the real
one so they run without a 150 MB download.
"""

import json

import numpy as np
import pandas as pd
import pytest

from zomato_reco.config import settings
from zomato_reco.data import repository
from zomato_reco.data.repository import (
    ArtifactMissingError,
    ArtifactStaleError,
    Repository,
    get_repository,
)
from zomato_reco.ingest.build_artifact import ARTIFACT_VERSION


@pytest.fixture
def tiny_artifact(tmp_path, monkeypatch):
    """Point settings at a two-row artifact in a temp directory."""
    df = pd.DataFrame(
        {
            "id": ["aaa", "bbb"],
            "name": ["Jalsa", "Toit"],
            "location": ["Whitefield", "Indiranagar"],
            "cuisines": [
                np.array(["North Indian"], dtype=object),
                np.array(["Italian"], dtype=object),
            ],
            "rating": [4.1, 4.7],
            "budget_band": ["high", "high"],
        }
    )
    facets = {
        "artifact_version": ARTIFACT_VERSION,
        "locations": ["Indiranagar", "Whitefield"],
        "cuisines": ["Italian", "North Indian"],
        "city_areas": ["BTM"],
        "meal_contexts": ["Dine-out"],
        "budget_bands": {"high": {"label": "over ₹500 for two"}},
        "rating": {"min": 1.8, "max": 4.9},
    }
    monkeypatch.setattr(settings, "processed_data_dir", tmp_path)
    df.to_parquet(tmp_path / settings.artifact_name, index=False)
    (tmp_path / settings.facets_name).write_text(json.dumps(facets))
    repository.reload()
    yield tmp_path
    repository.reload()


def test_get_repository_loads_artifact(tiny_artifact):
    repo = get_repository()
    assert len(repo) == 2
    assert repo.locations == ["Indiranagar", "Whitefield"]


def test_missing_artifact_gives_actionable_error(tmp_path, monkeypatch):
    """3.1 — must name the build command, not surface a bare FileNotFoundError."""
    monkeypatch.setattr(settings, "processed_data_dir", tmp_path)
    repository.reload()
    with pytest.raises(ArtifactMissingError) as exc:
        get_repository()
    assert "build_artifact" in str(exc.value)
    repository.reload()


def test_stale_artifact_is_rejected(tmp_path, monkeypatch):
    """7.6 — a cleaning-code change must not be silently paired with an old artifact."""
    monkeypatch.setattr(settings, "processed_data_dir", tmp_path)
    pd.DataFrame({"id": ["x"]}).to_parquet(tmp_path / settings.artifact_name, index=False)
    (tmp_path / settings.facets_name).write_text(json.dumps({"artifact_version": -99}))
    repository.reload()
    with pytest.raises(ArtifactStaleError) as exc:
        get_repository()
    assert "Rebuild" in str(exc.value)
    repository.reload()


def test_artifact_is_cached_across_calls(tiny_artifact):
    """Two calls must return the same object, not re-read the file."""
    assert repository.load_artifact() is repository.load_artifact()


def test_by_id_returns_row_or_none(tiny_artifact):
    repo = get_repository()
    assert repo.by_id("aaa")["name"] == "Jalsa"
    assert repo.by_id("does-not-exist") is None


def test_by_ids_preserves_requested_order(tiny_artifact):
    """Used to re-join dataset facts onto an LLM-supplied ranking, so order matters."""
    repo = get_repository()
    assert list(repo.by_ids(["bbb", "aaa"])["name"]) == ["Toit", "Jalsa"]


def test_by_ids_drops_unknown_ids(tiny_artifact):
    """5.1 — hallucinated ids must disappear here rather than raise."""
    repo = get_repository()
    result = repo.by_ids(["aaa", "hallucinated", "bbb"])
    assert list(result["id"]) == ["aaa", "bbb"]


def test_by_ids_empty_input(tiny_artifact):
    assert len(get_repository().by_ids([])) == 0


def test_budget_label(tiny_artifact):
    assert get_repository().budget_label("high") == "over ₹500 for two"


def test_repository_restaurants_is_not_mutated_by_filters(tiny_artifact):
    repo = get_repository()
    original = len(repo.restaurants)
    _ = repo.restaurants[repo.restaurants["location"] == "Whitefield"]
    assert len(repo.restaurants) == original


def test_repository_accepts_injected_frame():
    """No file access needed when a frame is supplied directly, which keeps tests fast."""
    df = pd.DataFrame({"id": ["z"], "name": ["Test"]})
    repo = Repository(df, {"locations": [], "cuisines": []})
    assert len(repo) == 1
