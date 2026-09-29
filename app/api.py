"""HTTP API for the Stitch frontend.

Business logic stays in `pipeline.recommend`. This module only loads the artifact
once, validates the request, and returns the existing result contract.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from zomato_reco.config import settings
from zomato_reco.data.repository import (
    ArtifactMissingError,
    ArtifactStaleError,
    Repository,
    get_repository,
)
from zomato_reco.models import RecommendationResult, UserPreferences
from zomato_reco.recommend.pipeline import recommend as recommend_restaurants

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    ok: bool
    restaurants: int
    llm: bool


class FacetsResponse(BaseModel):
    artifact_version: int
    restaurant_count: int
    locations: list[str]
    cuisines: list[str]
    meal_contexts: list[str]
    budget_bands: dict[str, Any]
    rating: dict[str, float] = Field(
        description="Parsed rating floor and ceiling in the artifact."
    )


def resolve_cors_origins() -> list[str]:
    """Browser origins for CORS.

    A wildcard is refused once a Groq key is configured, so a public site cannot
    invite arbitrary pages to spend the key's quota.
    """
    origins = settings.cors_origin_list
    if "*" in origins and settings.llm_enabled:
        logger.warning("Ignoring CORS wildcard because a Groq API key is configured")
        origins = [origin for origin in origins if origin != "*"]
    return origins


def _facets_payload(repo: Repository) -> FacetsResponse:
    facets = repo.facets
    rating = facets["rating"]
    return FacetsResponse(
        artifact_version=facets["artifact_version"],
        restaurant_count=facets["restaurant_count"],
        locations=repo.locations,
        cuisines=repo.cuisines,
        meal_contexts=repo.meal_contexts,
        budget_bands=repo.budget_bands,
        rating={"min": float(rating["min"]), "max": float(rating["max"])},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.repo = get_repository()
        app.state.artifact_error = None
        logger.info("Loaded %s restaurants", f"{len(app.state.repo):,}")
    except (ArtifactMissingError, ArtifactStaleError) as exc:
        app.state.repo = None
        app.state.artifact_error = str(exc)
        logger.error("Artifact unavailable: %s", exc)
    yield


app = FastAPI(title="Bangalore restaurant recommender", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _require_repo() -> Repository:
    repo = getattr(app.state, "repo", None)
    if repo is None:
        detail = getattr(app.state, "artifact_error", None) or "Artifact unavailable"
        raise HTTPException(status_code=503, detail=detail)
    return repo


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness. Does not call Groq."""
    repo = _require_repo()
    return HealthResponse(ok=True, restaurants=len(repo), llm=settings.llm_enabled)


@app.get("/api/facets", response_model=FacetsResponse)
def facets() -> FacetsResponse:
    """Dropdown values and the calibrated budget labels."""
    return _facets_payload(_require_repo())


@app.post("/api/recommend", response_model=RecommendationResult)
def recommend(prefs: UserPreferences | None = None) -> RecommendationResult:
    """Rank restaurants. An empty object is a citywide query."""
    return recommend_restaurants(prefs or UserPreferences(), _require_repo())
