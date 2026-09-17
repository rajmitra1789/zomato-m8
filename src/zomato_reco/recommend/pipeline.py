"""Recommendation pipeline: filter -> relax -> score -> rank -> results.

This module is the deterministic path and needs no API key. Phase 5 adds an optional LLM
reranking step on top of `select_candidates`; when the LLM is unavailable or misbehaves,
`recommend` as written here is exactly the fallback.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd

from zomato_reco.config import settings
from zomato_reco.data.repository import Repository, get_repository
from zomato_reco.models import (
    Recommendation,
    RecommendationResult,
    Relaxation,
    Restaurant,
    UserPreferences,
)
from zomato_reco.recommend.relaxation import relax
from zomato_reco.recommend.scoring import cap_per_brand, explain, highlights, rank, score

logger = logging.getLogger(__name__)

# Columns carrying internal sort keys, which never leave this module.
_INTERNAL_COLUMNS = ("_has_rating", "_base_score", "_boost", "_strict")

_RESTAURANT_FIELDS = (
    "id",
    "name",
    "location",
    "city_areas",
    "address",
    "cuisines",
    "rest_types",
    "meal_contexts",
    "rating",
    "votes",
    "weighted_rating",
    "cost_for_two",
    "budget_band",
    "online_order",
    "book_table",
    "is_family_friendly",
    "is_quick_service",
    "dish_liked",
    "review_snippets",
    "url",
)


@dataclass
class CandidateSelection:
    """The candidate pool handed to the LLM, plus how it was obtained."""

    df: pd.DataFrame
    relaxations: list[Relaxation] = field(default_factory=list)
    matched_before_cap: int = 0

    @property
    def ids(self) -> list[str]:
        return list(self.df["id"])

    @property
    def is_empty(self) -> bool:
        return self.df.empty


def to_restaurant(row: pd.Series) -> Restaurant:
    """Convert an artifact row into the typed contract, coercing Parquet NaN to None."""
    return Restaurant(**{f: row[f] for f in _RESTAURANT_FIELDS if f in row.index})


def select_candidates(
    prefs: UserPreferences,
    repo: Repository | None = None,
    *,
    limit: int | None = None,
) -> CandidateSelection:
    """Filter, relax if needed, score, rank, cap per brand, and truncate.

    The brand cap is applied to the candidate pool rather than only the final results, so
    the LLM is never handed fifteen outlets of the same chain to choose between.
    """
    repo = repo or get_repository()
    limit = settings.candidate_count if limit is None else limit

    outcome = relax(repo.restaurants, prefs, known_locations=repo.locations)
    if outcome.df.empty:
        return CandidateSelection(df=outcome.df, relaxations=outcome.relaxations)

    scored = score(outcome.df, prefs.extras, strict_ids=outcome.strict_ids)
    ranked = rank(scored)
    matched = len(ranked)
    capped = cap_per_brand(ranked)

    return CandidateSelection(
        df=capped.head(limit),
        relaxations=outcome.relaxations,
        matched_before_cap=matched,
    )


def recommend(
    prefs: UserPreferences,
    repo: Repository | None = None,
) -> RecommendationResult:
    """Deterministic recommendations with template explanations.

    Returns fewer than `prefs.result_count` when fewer exist rather than padding with
    restaurants that match nothing the user asked for.
    """
    repo = repo or get_repository()
    selection = select_candidates(prefs, repo)

    if selection.is_empty:
        logger.info("No candidates for %s", prefs.model_dump(exclude_defaults=True))
        return RecommendationResult(
            restaurants=[],
            recommendations=[],
            relaxations=selection.relaxations,
            llm_used=False,
        )

    top = selection.df.head(prefs.result_count)
    restaurants = [to_restaurant(row) for _, row in top.iterrows()]
    recommendations = [
        Recommendation(
            id=row["id"],
            rank=position,
            explanation=explain(row, prefs.cuisines),
            match_highlights=highlights(row),
        )
        for position, (_, row) in enumerate(top.iterrows(), start=1)
    ]

    logger.info(
        "Returned %d of %d matches (relaxations: %s)",
        len(restaurants),
        selection.matched_before_cap,
        [r.constraint for r in selection.relaxations] or "none",
    )

    return RecommendationResult(
        restaurants=restaurants,
        recommendations=recommendations,
        summary=None,
        relaxations=selection.relaxations,
        llm_used=False,
    )


def drop_internal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip scoring scratch columns, for callers that want to display the frame."""
    return df.drop(columns=[c for c in _INTERNAL_COLUMNS if c in df.columns])
