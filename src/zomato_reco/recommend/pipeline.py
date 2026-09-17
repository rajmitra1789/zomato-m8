"""Recommendation pipeline: filter -> relax -> score -> rank -> (optional LLM) -> results.

Phase 4 is the deterministic path and needs no API key. Phase 5 optionally reranks the
same candidates through Groq; when the LLM is unavailable or misbehaves, the Phase 4
output is returned unchanged.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd

from zomato_reco.config import settings
from zomato_reco.data.repository import Repository, get_repository
from zomato_reco.llm.base import LLMClient
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
    repo = repo if repo is not None else get_repository()
    limit = settings.candidate_count if limit is None else limit

    if repo.restaurants.empty:
        return CandidateSelection(df=repo.restaurants)

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
    llm: LLMClient | None = None,
) -> RecommendationResult:
    """Recommendations, with Groq ranking when a client is available.

    `llm` is injected in tests. In production it is constructed only when
    `settings.llm_enabled` is true. Every LLM failure degrades to the deterministic
    Phase 4 result rather than raising.
    """
    repo = repo if repo is not None else get_repository()
    selection = select_candidates(prefs, repo)

    if selection.is_empty:
        logger.info("No candidates for %s", prefs.model_dump(exclude_defaults=True))
        return RecommendationResult(
            restaurants=[],
            recommendations=[],
            relaxations=selection.relaxations,
            llm_used=False,
        )

    client = llm if llm is not None else _maybe_client()
    # Ranking one restaurant is a wasted call (edge-case.md 5.9 / 5.10).
    if client is None or len(selection.df) < 2:
        return _deterministic(selection, prefs)

    try:
        return _llm_rank(selection, prefs, client)
    except Exception:
        logger.exception("LLM path failed; falling back to deterministic ranking")
        return _deterministic(selection, prefs)


def _maybe_client() -> LLMClient | None:
    if not settings.llm_enabled:
        return None
    from zomato_reco.llm.openai_client import get_client

    try:
        return get_client()
    except Exception:
        logger.exception("Could not construct LLM client; using deterministic ranking")
        return None


def _llm_rank(
    selection: CandidateSelection, prefs: UserPreferences, client: LLMClient
) -> RecommendationResult:
    from zomato_reco.llm.parser import gate
    from zomato_reco.llm.prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_message

    explanations, chips = _template_maps(selection.df, prefs)
    payload = client.complete_json(
        SYSTEM_PROMPT,
        build_user_message(prefs, selection.df, selection.relaxations),
        RESPONSE_SCHEMA,
    )
    gated = gate(
        payload,
        candidate_ids=selection.ids,
        requested=prefs.result_count,
        fallback_explanations=explanations,
        fallback_highlights=chips,
    )
    if not gated.recommendations:
        return _deterministic(selection, prefs)

    logger.info(
        "LLM gate dropped=%s backfilled=%s",
        gated.dropped_ids or "none",
        gated.backfilled_ids or "none",
    )
    return _assemble(selection, gated.recommendations, gated.summary, gated.used_model_prose)


def _deterministic(selection: CandidateSelection, prefs: UserPreferences) -> RecommendationResult:
    top = selection.df.head(prefs.result_count)
    recs = [
        Recommendation(
            id=row["id"],
            rank=position,
            explanation=explain(row, prefs.cuisines),
            match_highlights=highlights(row),
        )
        for position, (_, row) in enumerate(top.iterrows(), start=1)
    ]
    logger.info(
        "Returned %d of %d matches (relaxations: %s, llm=no)",
        len(recs),
        selection.matched_before_cap,
        [r.constraint for r in selection.relaxations] or "none",
    )
    return _assemble(selection, recs, summary=None, llm_used=False)


def _template_maps(
    df: pd.DataFrame, prefs: UserPreferences
) -> tuple[dict[str, str], dict[str, list[str]]]:
    explanations: dict[str, str] = {}
    chips: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        ident = row["id"]
        explanations[ident] = explain(row, prefs.cuisines)
        chips[ident] = highlights(row)
    return explanations, chips


def _assemble(
    selection: CandidateSelection,
    recs: list[Recommendation],
    summary: str | None,
    llm_used: bool,
) -> RecommendationResult:
    by_id = {row["id"]: row for _, row in selection.df.iterrows()}
    restaurants = [to_restaurant(by_id[rec.id]) for rec in recs if rec.id in by_id]
    recs = [rec for rec in recs if rec.id in by_id]
    return RecommendationResult(
        restaurants=restaurants,
        recommendations=recs,
        summary=summary,
        relaxations=selection.relaxations,
        llm_used=llm_used,
    )


def drop_internal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip scoring scratch columns, for callers that want to display the frame."""
    return df.drop(columns=[c for c in _INTERNAL_COLUMNS if c in df.columns])
