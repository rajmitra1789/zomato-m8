"""Tests for the ingestion pipeline's derived columns.

Case IDs refer to docs/edge-case.md §2. The full-run invariants live in the build itself
(`_validate`) rather than here, since they need the real 150 MB dataset.
"""

import pandas as pd
import pytest
from pydantic import ValidationError

from zomato_reco.config import Settings
from zomato_reco.ingest.build_artifact import (
    _aggregate_siblings,
    _budget_band,
    _flags,
    _make_id,
    _weighted_rating,
)

P33, P66 = 300.0, 500.0


# ---------------------------------------------------------------------- budget bands


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (40, "low"),  # dataset minimum
        (299, "low"),
        (300, "low"),  # 2.4 boundary: 1,921 restaurants sit exactly here
        (301, "medium"),
        (499, "medium"),
        (500, "medium"),  # 2.4 boundary: 1,258 restaurants sit exactly here
        (501, "high"),
        (6000, "high"),  # dataset maximum
        (None, None),  # 2.7 cost missing -> no band, never "low"
    ],
)
def test_budget_band_boundaries(cost, expected):
    assert _budget_band(cost, P33, P66) == expected


def test_budget_band_boundaries_are_inclusive_low():
    """An off-by-one here moves ~1,900 restaurants between bands."""
    assert _budget_band(P33, P33, P66) == "low"
    assert _budget_band(P66, P33, P66) == "medium"


def test_budget_band_handles_nan():
    assert _budget_band(float("nan"), P33, P66) is None


# ------------------------------------------------------------------ weighted rating


def test_weighted_rating_zero_votes_equals_global_mean():
    """2.5 — 3,186 restaurants have zero votes; no division by zero, result is exactly C."""
    assert _weighted_rating(4.9, 0, mean_rating=3.625, m=50) == pytest.approx(3.625)


def test_weighted_rating_unrated_is_none_not_zero():
    """0.0 would rank an unrated restaurant below a genuine 1.8."""
    assert _weighted_rating(None, 500, mean_rating=3.625, m=50) is None
    assert _weighted_rating(float("nan"), 500, mean_rating=3.625, m=50) is None


def test_weighted_rating_high_votes_approaches_raw_rating():
    assert _weighted_rating(4.5, 3000, mean_rating=3.625, m=50) == pytest.approx(4.486, abs=0.01)


def test_weighted_rating_low_votes_shrinks_toward_mean():
    """The property that stops a 4.9-from-4-votes outranking a 4.5-from-3,000."""
    hyped = _weighted_rating(4.9, 4, mean_rating=3.625, m=50)
    proven = _weighted_rating(4.5, 3000, mean_rating=3.625, m=50)
    assert hyped < proven


def test_weighted_rating_negative_votes_treated_as_zero():
    assert _weighted_rating(4.0, -5, mean_rating=3.625, m=50) == pytest.approx(3.625)


def test_min_votes_m_must_be_positive():
    """4.8 — m = 0 would divide by zero for the 3,186 zero-vote restaurants."""
    with pytest.raises(ValidationError):
        Settings(min_votes_m=0)


# ------------------------------------------------------------------------------- id


def test_make_id_is_stable_and_content_derived():
    """A rebuild must not change ids, or cached LLM responses and snapshots break."""
    key = "https://www.zomato.com/bangalore/jalsa-banashankari"
    assert _make_id(key) == _make_id(key)
    assert len(_make_id(key)) == 12


def test_make_id_differs_per_restaurant():
    a = _make_id("https://www.zomato.com/bangalore/jalsa-banashankari")
    b = _make_id("https://www.zomato.com/bangalore/jalsa-jayanagar")
    assert a != b


# ---------------------------------------------------------------------------- flags


def test_flags_family_friendly_from_rest_type():
    family, _ = _flags(["Casual Dining"], ["Dine-out"])
    assert family is True


def test_flags_family_friendly_from_buffet_context():
    family, _ = _flags(["Quick Bites"], ["Buffet"])
    assert family is True


def test_flags_alcohol_venue_is_not_family_friendly():
    family, _ = _flags(["Casual Dining", "Bar"], ["Dine-out"])
    assert family is False


def test_flags_quick_service_from_rest_type():
    _, quick = _flags(["Quick Bites"], ["Dine-out"])
    assert quick is True


def test_flags_quick_service_from_delivery_context():
    _, quick = _flags(["Casual Dining"], ["Delivery"])
    assert quick is True


def test_flags_empty_inputs_are_false_not_error():
    """1.8 — 63 restaurants have no rest_type at all."""
    assert _flags([], []) == (False, False)


# ---------------------------------------------------------------- sibling aggregation


def test_aggregate_siblings_preserves_all_listing_values():
    """2.8 — a restaurant appears in up to 14 search areas; keeping one would lose 13."""
    df = pd.DataFrame(
        {
            "dedup_key": ["a", "a", "a", "b"],
            "meal_context": ["Buffet", "Dine-out", "Buffet", "Delivery"],
            "city_area": ["BTM", "Jayanagar", "HSR", "Whitefield"],
        }
    )
    out = _aggregate_siblings(df)
    assert out.loc["a", "meal_contexts"] == ["Buffet", "Dine-out"]
    assert out.loc["a", "city_areas"] == ["BTM", "HSR", "Jayanagar"]
    assert out.loc["a", "listing_count"] == 3
    assert out.loc["b", "meal_contexts"] == ["Delivery"]


def test_restaurant_model_coerces_nan_to_none():
    """Parquet returns NaN for missing numerics, and NaN satisfies `float | None`.

    Without coercion, `rating is None` checks silently fail and the UI renders "nan".
    """
    from zomato_reco.models import Restaurant

    r = Restaurant(
        id="x",
        name="Unrated Place",
        rating=float("nan"),
        weighted_rating=float("nan"),
        cost_for_two=float("nan"),
    )
    assert r.rating is None
    assert r.weighted_rating is None
    assert r.cost_for_two is None


def test_restaurant_model_accepts_numpy_arrays_from_parquet():
    """List columns come back from Parquet as ndarray, not list."""
    import numpy as np

    from zomato_reco.models import Restaurant

    r = Restaurant(id="x", name="Y", cuisines=np.array(["North Indian", "Chinese"]))
    assert r.cuisines == ["North Indian", "Chinese"]


def test_aggregate_siblings_ignores_missing_values():
    df = pd.DataFrame(
        {
            "dedup_key": ["a", "a"],
            "meal_context": ["Buffet", None],
            "city_area": [None, "BTM"],
        }
    )
    out = _aggregate_siblings(df)
    assert out.loc["a", "meal_contexts"] == ["Buffet"]
    assert out.loc["a", "city_areas"] == ["BTM"]
