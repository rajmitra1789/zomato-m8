"""Typed contracts between layers.

`Restaurant` is the cleaned record written by ingestion and treated as the sole source of
truth for facts. `Recommendation` carries only what the LLM is allowed to contribute:
ordering and prose. Facts are re-joined from `Restaurant` by id.
"""

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

BudgetBand = Literal["low", "medium", "high"]


def _nan_to_none(value: Any) -> Any:
    """Parquet hands back NaN for missing numerics, and NaN satisfies `float | None`.

    Left alone it would defeat the whole unrated contract: `is None` checks silently fail
    and the UI renders the literal text "nan".
    """
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class Restaurant(BaseModel):
    """One deduplicated restaurant. Every displayed fact originates here."""

    id: str
    name: str
    location: str | None = None
    # A restaurant is listed in up to 14 Zomato search areas; this is not a parent region.
    city_areas: list[str] = Field(default_factory=list)
    address: str | None = None
    cuisines: list[str] = Field(default_factory=list)
    rest_types: list[str] = Field(default_factory=list)
    meal_contexts: list[str] = Field(default_factory=list)

    # None for unrated, "NEW", and "-" — never coerced to 0.0, which would read as a bad score.
    rating: float | None = None
    votes: int = 0
    # None when unrated: "unknown" is not the same claim as "scored zero". Phase 4 sorts
    # these last explicitly rather than letting a 0.0 do it implicitly.
    weighted_rating: float | None = None

    cost_for_two: int | None = None
    budget_band: BudgetBand | None = None

    online_order: bool = False
    book_table: bool = False
    is_family_friendly: bool = False
    is_quick_service: bool = False

    dish_liked: list[str] = Field(default_factory=list)
    review_snippets: list[str] = Field(default_factory=list)
    url: str | None = None

    @field_validator("rating", "weighted_rating", "cost_for_two", mode="before")
    @classmethod
    def _coerce_nan(cls, value: Any) -> Any:
        return _nan_to_none(value)


class UserPreferences(BaseModel):
    """Validated user input. Every field optional: an empty form is a valid query."""

    location: str | None = None
    budget: BudgetBand | None = None
    cuisines: list[str] = Field(default_factory=list)
    min_rating: float | None = Field(default=None, ge=0.0, le=5.0)
    meal_context: str | None = None
    extras: str = ""
    result_count: int = Field(default=5, ge=1, le=20)

    @field_validator("extras")
    @classmethod
    def _bound_extras(cls, v: str) -> str:
        """Free text reaches the prompt, so bound it. Injection is handled at the LLM layer."""
        return v.strip()[:500]


class Relaxation(BaseModel):
    """A constraint the pipeline had to loosen, surfaced to the user rather than hidden."""

    constraint: Literal["meal_context", "cuisine", "min_rating", "location", "budget"]
    detail: str


class Recommendation(BaseModel):
    """The LLM's contribution for one restaurant: rank and prose only, no facts."""

    id: str
    rank: int
    explanation: str
    match_highlights: list[str] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    """What the UI renders. `restaurants` holds the authoritative facts."""

    restaurants: list[Restaurant]
    recommendations: list[Recommendation]
    summary: str | None = None
    relaxations: list[Relaxation] = Field(default_factory=list)
    llm_used: bool = False
