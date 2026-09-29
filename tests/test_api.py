"""HTTP contract for the Railway API. No Groq key required."""

import pytest
from fastapi.testclient import TestClient

from zomato_reco.config import settings
from zomato_reco.data.repository import ArtifactMissingError


@pytest.fixture(scope="module")
def client():
    from app.api import app

    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["restaurants"] == 12426
    assert body["llm"] is False


def test_facets(client: TestClient):
    response = client.get("/api/facets")
    assert response.status_code == 200
    body = response.json()
    assert body["artifact_version"] == 1
    assert body["restaurant_count"] == 12426
    assert len(body["locations"]) == 93
    assert len(body["meal_contexts"]) == 7
    assert body["budget_bands"]["low"]["label"] == "up to ₹300 for two"
    assert body["rating"]["min"] == 1.8
    assert body["rating"]["max"] == 4.9


def test_recommend_respects_result_count(client: TestClient):
    response = client.post(
        "/api/recommend",
        json={"location": "HSR", "budget": "low", "result_count": 3},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["llm_used"] is False
    assert 1 <= len(body["restaurants"]) <= 3
    assert len(body["restaurants"]) == len(body["recommendations"])
    assert body["restaurants"][0]["name"]
    assert "rating" in body["restaurants"][0]


def test_empty_body_is_a_citywide_query(client: TestClient):
    response = client.post("/api/recommend", json={})
    assert response.status_code == 200
    body = response.json()
    assert len(body["restaurants"]) == 5
    assert body["llm_used"] is False


def test_invalid_rating_is_rejected(client: TestClient):
    response = client.post("/api/recommend", json={"min_rating": 9})
    assert response.status_code == 422


def test_cors_allows_local_frontend(client: TestClient):
    response = client.options(
        "/api/recommend",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_missing_artifact_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app import api

    monkeypatch.setattr(api.app.state, "repo", None)
    monkeypatch.setattr(
        api.app.state,
        "artifact_error",
        str(ArtifactMissingError("Artifact not found at data/processed/restaurants.parquet")),
    )
    response = client.get("/health")
    assert response.status_code == 503
    assert "restaurants.parquet" in response.json()["detail"]


def test_wildcard_cors_dropped_when_llm_key_is_set(monkeypatch: pytest.MonkeyPatch):
    from app.api import resolve_cors_origins

    monkeypatch.setattr(settings, "openai_api_key", "gsk-test")
    monkeypatch.setattr(settings, "cors_origins", "https://app.vercel.app,*")
    assert resolve_cors_origins() == ["https://app.vercel.app"]
