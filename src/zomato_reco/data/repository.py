"""Access to the cleaned artifact.

The artifact is loaded once per process and treated as read-only. Callers get the
DataFrame for filtering and the facets for populating UI controls; nothing here mutates
state, so concurrent Streamlit sessions can share a single instance safely.
"""

import json
import logging
from functools import lru_cache

import pandas as pd

from zomato_reco.config import settings
from zomato_reco.ingest.build_artifact import ARTIFACT_VERSION

logger = logging.getLogger(__name__)

_BUILD_COMMAND = "python -m zomato_reco.ingest.build_artifact"


class ArtifactMissingError(RuntimeError):
    """The artifact has not been built yet."""


class ArtifactStaleError(RuntimeError):
    """The artifact was built by an incompatible version of the cleaning code."""


@lru_cache(maxsize=1)
def load_facets() -> dict:
    """Facet values and calibrated thresholds emitted by ingestion."""
    path = settings.facets_path
    if not path.exists():
        raise ArtifactMissingError(
            f"Facets not found at {path}.\nBuild it first:\n  {_BUILD_COMMAND}"
        )

    facets = json.loads(path.read_text())
    version = facets.get("artifact_version")
    if version != ARTIFACT_VERSION:
        raise ArtifactStaleError(
            f"Artifact was built by version {version}, but this code expects "
            f"{ARTIFACT_VERSION}. Rebuild it:\n  {_BUILD_COMMAND}"
        )
    return facets


@lru_cache(maxsize=1)
def load_artifact() -> pd.DataFrame:
    """The deduplicated restaurant table, cached for the process lifetime."""
    path = settings.artifact_path
    if not path.exists():
        raise ArtifactMissingError(
            f"Artifact not found at {path}.\nBuild it first:\n  {_BUILD_COMMAND}"
        )

    df = pd.read_parquet(path)
    logger.info("Loaded %s restaurants from %s", f"{len(df):,}", path.name)
    return df


def reload() -> None:
    """Drop the caches so the next access re-reads from disk."""
    load_artifact.cache_clear()
    load_facets.cache_clear()


class Repository:
    """Read-only view over the artifact, plus convenience accessors for the UI."""

    def __init__(self, df: pd.DataFrame, facets: dict) -> None:
        self._df = df
        self._facets = facets

    # --- data ---

    @property
    def restaurants(self) -> pd.DataFrame:
        """The full table. Filters return new frames; this one is never mutated."""
        return self._df

    def __len__(self) -> int:
        return len(self._df)

    def by_id(self, restaurant_id: str) -> pd.Series | None:
        match = self._df[self._df["id"] == restaurant_id]
        return None if match.empty else match.iloc[0]

    def by_ids(self, ids: list[str]) -> pd.DataFrame:
        """Rows for the given ids, in the order supplied.

        Used to re-join authoritative facts onto an LLM-supplied ordering.
        """
        indexed = self._df.set_index("id")
        known = [i for i in ids if i in indexed.index]
        return indexed.loc[known].reset_index()

    # --- facets ---

    @property
    def facets(self) -> dict:
        return self._facets

    @property
    def locations(self) -> list[str]:
        return self._facets["locations"]

    @property
    def cuisines(self) -> list[str]:
        return self._facets["cuisines"]

    @property
    def city_areas(self) -> list[str]:
        return self._facets["city_areas"]

    @property
    def meal_contexts(self) -> list[str]:
        return self._facets["meal_contexts"]

    @property
    def budget_bands(self) -> dict:
        """Calibrated rupee ranges, so UI labels can state real numbers."""
        return self._facets["budget_bands"]

    @property
    def rating_bounds(self) -> dict:
        return self._facets["rating"]

    def budget_label(self, band: str) -> str:
        return self._facets["budget_bands"][band]["label"]


def get_repository() -> Repository:
    """The shared repository instance."""
    return Repository(load_artifact(), load_facets())
