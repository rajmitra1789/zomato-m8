"""Tests for the hard constraint filters.

Case IDs refer to docs/edge-case.md §3. The frame below is hand-built but mirrors the real
data's shapes: numpy arrays in list columns, NaN ratings, None budget bands, and the
cuisine vocabulary's substring traps.
"""

import numpy as np
import pandas as pd
import pytest

from zomato_reco.models import UserPreferences
from zomato_reco.recommend.filters import (
    apply_filters,
    filter_budget,
    filter_cuisines,
    filter_location,
    filter_meal_context,
    filter_min_rating,
)


@pytest.fixture
def df() -> pd.DataFrame:
    """Eight restaurants covering the cases that matter."""
    rows = [
        # id, name, location, cuisines, meal_contexts, rating, cost, band
        ("r1", "Jalsa", "Whitefield", ["North Indian", "Chinese"], ["Dine-out"], 4.1, 800, "high"),
        (
            "r2",
            "Toit",
            "Indiranagar",
            ["Italian", "Pizza"],
            ["Dine-out", "Buffet"],
            4.7,
            1500,
            "high",
        ),
        ("r3", "Empire", "Whitefield", ["Biryani"], ["Delivery"], 4.0, 400, "medium"),
        ("r4", "Sagar", "Whitefield", ["South Indian"], ["Delivery"], 3.2, 200, "low"),
        # unrated — must vanish the moment a minimum rating is set (3.3, 3.5)
        ("r5", "New Place", "Whitefield", ["Italian"], ["Dine-out"], np.nan, 300, "low"),
        # no cost, so no band — must never be treated as "low" (3.10)
        ("r6", "No Price", "Whitefield", ["Cafe"], ["Cafes"], 4.5, None, None),
        # the substring traps: 'Indian' and 'Mithai' are distinct cuisines (3.9)
        ("r7", "Plain Indian", "Jayanagar", ["Indian"], ["Dine-out"], 3.9, 500, "medium"),
        ("r8", "Sweet Corner", "Jayanagar", ["Mithai", "Desserts"], ["Desserts"], 3.5, 150, "low"),
    ]
    return pd.DataFrame(
        {
            "id": [r[0] for r in rows],
            "name": [r[1] for r in rows],
            "location": [r[2] for r in rows],
            "cuisines": [np.array(r[3], dtype=object) for r in rows],
            "meal_contexts": [np.array(r[4], dtype=object) for r in rows],
            "rating": [r[5] for r in rows],
            "cost_for_two": [r[6] for r in rows],
            "budget_band": [r[7] for r in rows],
        }
    )


def ids(frame: pd.DataFrame) -> set[str]:
    return set(frame["id"])


# -------------------------------------------------------------------------- location


def test_filter_location_exact_match(df):
    assert ids(filter_location(df, "Whitefield")) == {"r1", "r3", "r4", "r5", "r6"}


def test_filter_location_is_case_and_whitespace_insensitive(df):
    """3.11 — facet values and user input may differ in casing."""
    assert ids(filter_location(df, "  whitefield ")) == ids(filter_location(df, "Whitefield"))


def test_filter_location_unknown_returns_empty(df):
    assert len(filter_location(df, "Atlantis")) == 0


def test_filter_location_none_is_noop(df):
    assert len(filter_location(df, None)) == len(df)


# ---------------------------------------------------------------------------- budget


def test_filter_budget_exact_band(df):
    assert ids(filter_budget(df, "low")) == {"r4", "r5", "r8"}


def test_filter_budget_excludes_rows_without_a_band(df):
    """3.10 — a missing cost must not be presented as cheap."""
    for band in ("low", "medium", "high"):
        assert "r6" not in ids(filter_budget(df, band))


def test_filter_budget_at_or_below(df):
    assert ids(filter_budget(df, "medium", at_or_below=True)) == {"r3", "r4", "r5", "r7", "r8"}


# ---------------------------------------------------------------------- min rating


def test_filter_min_rating_excludes_unrated(df):
    """3.3, 3.5 — never compare NaN against the threshold; drop unrated explicitly."""
    result = filter_min_rating(df, 4.0)
    assert ids(result) == {"r1", "r2", "r3", "r6"}
    assert "r5" not in ids(result)


def test_filter_min_rating_boundary_is_inclusive(df):
    assert "r3" in ids(filter_min_rating(df, 4.0))  # r3 is exactly 4.0


def test_filter_min_rating_infeasible_threshold_returns_empty(df):
    """3.4 — only 22 restaurants citywide reach 4.8; empty must not raise."""
    assert len(filter_min_rating(df, 4.9)) == 0


def test_filter_min_rating_none_keeps_unrated(df):
    assert "r5" in ids(filter_min_rating(df, None))


# --------------------------------------------------------------------------- cuisine


def test_filter_cuisines_exact_token_not_substring(df):
    """3.9 — the single most important filter correctness case.

    'Indian' must match only the restaurant serving 'Indian', not the ones serving
    'North Indian' or 'South Indian'.
    """
    assert ids(filter_cuisines(df, ["Indian"])) == {"r7"}


def test_filter_cuisines_does_not_match_mithai_for_thai(df):
    """'Thai' is a substring of 'Mithai', an Indian sweet. Nothing should match here."""
    assert len(filter_cuisines(df, ["Thai"])) == 0


def test_filter_cuisines_north_indian_is_distinct_from_indian(df):
    assert ids(filter_cuisines(df, ["North Indian"])) == {"r1"}


def test_filter_cuisines_any_overlap_not_all(df):
    """3.8 — multi-select broadens. Requiring both would return nothing."""
    assert ids(filter_cuisines(df, ["Italian", "Biryani"])) == {"r2", "r3", "r5"}


def test_filter_cuisines_is_case_insensitive(df):
    assert ids(filter_cuisines(df, ["italian"])) == ids(filter_cuisines(df, ["Italian"]))


def test_filter_cuisines_rare_cuisine_returns_its_single_match(df):
    """3.6 — Vegan, Russian and Sindhi each have exactly one restaurant citywide."""
    assert ids(filter_cuisines(df, ["Mithai"])) == {"r8"}


def test_filter_cuisines_empty_and_blank_are_noop(df):
    assert len(filter_cuisines(df, [])) == len(df)
    assert len(filter_cuisines(df, ["  "])) == len(df)


# ---------------------------------------------------------------------- meal context


def test_filter_meal_context(df):
    assert ids(filter_meal_context(df, "Delivery")) == {"r3", "r4"}


def test_filter_meal_context_matches_any_of_several(df):
    assert "r2" in ids(filter_meal_context(df, "Buffet"))


# ------------------------------------------------------------------------- composed


def test_apply_filters_combines_constraints(df):
    prefs = UserPreferences(location="Whitefield", budget="high", min_rating=4.0)
    assert ids(apply_filters(df, prefs)) == {"r1"}


def test_apply_filters_empty_preferences_is_noop(df):
    """6.3 — an empty form is a valid query, not an error."""
    assert len(apply_filters(df, UserPreferences())) == len(df)


def test_apply_filters_over_constrained_returns_empty_without_raising(df):
    """3.7 — 27 of 93 locations have no Italian at all."""
    prefs = UserPreferences(location="Jayanagar", cuisines=["Italian"], min_rating=4.5)
    assert len(apply_filters(df, prefs)) == 0


def test_apply_filters_order_does_not_change_result(df):
    """Filters must be commutative, so the ordering is purely a performance choice."""
    prefs = UserPreferences(location="Whitefield", cuisines=["Italian"], min_rating=3.0)
    composed = apply_filters(df, prefs)
    manual = filter_cuisines(filter_min_rating(filter_location(df, "Whitefield"), 3.0), ["Italian"])
    assert ids(composed) == ids(manual)


def test_apply_filters_does_not_mutate_input(df):
    before = len(df)
    apply_filters(df, UserPreferences(location="Whitefield", min_rating=4.0))
    assert len(df) == before
