"""Tests for scoring, ranking, brand capping, and relaxation.

Case IDs refer to docs/edge-case.md §4. The two properties the plan calls out explicitly:
a hyped low-vote restaurant must not outrank a proven one, and relaxation must both trigger
below the floor and record what it changed.
"""

import numpy as np
import pandas as pd
import pytest

from zomato_reco.models import UserPreferences
from zomato_reco.recommend.relaxation import (
    adjacent_locations,
    related_cuisines,
    relax,
)
from zomato_reco.recommend.scoring import (
    cap_per_brand,
    explain,
    highlights,
    rank,
    score,
    soft_preference_boost,
)


def make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a frame with the artifact's shapes: numpy arrays, NaN ratings, None bands."""
    defaults = {
        "id": "x",
        "name": "Place",
        "location": "Whitefield",
        "cuisines": [],
        "rest_types": [],
        "meal_contexts": [],
        "dish_liked": [],
        "rating": 4.0,
        "votes": 100,
        "weighted_rating": 4.0,
        "cost_for_two": 400,
        "budget_band": "medium",
        "online_order": False,
        "book_table": False,
        "is_family_friendly": False,
        "is_quick_service": False,
    }
    filled = []
    for row in rows:
        merged = {**defaults, **row}
        for key in ("cuisines", "rest_types", "meal_contexts", "dish_liked"):
            merged[key] = np.array(merged[key], dtype=object)
        filled.append(merged)
    return pd.DataFrame(filled)


# ------------------------------------------------------------------- vote confidence


def test_low_vote_hype_does_not_outrank_proven_restaurant():
    """The property the plan names: 4.9 from 4 votes must lose to 4.5 from 3,000.

    Uses the same shrinkage ingestion applies, so this asserts the end-to-end ordering
    rather than the formula in isolation.
    """
    C, m = 3.625, 50
    hyped = (4 / (4 + m)) * 4.9 + (m / (4 + m)) * C
    proven = (3000 / (3000 + m)) * 4.5 + (m / (3000 + m)) * C
    df = make_df(
        [
            {
                "id": "hyped",
                "name": "New Hype",
                "rating": 4.9,
                "votes": 4,
                "weighted_rating": hyped,
            },
            {
                "id": "proven",
                "name": "Old Faithful",
                "rating": 4.5,
                "votes": 3000,
                "weighted_rating": proven,
            },
        ]
    )
    assert list(rank(score(df))["id"]) == ["proven", "hyped"]


# ---------------------------------------------------------------------- determinism


def test_ranking_is_deterministic_across_runs():
    """4.2 — 147 real restaurants share an identical (rating, cost), so ties are common."""
    df = make_df(
        [
            {
                "id": f"r{i}",
                "name": f"Place {i}",
                "rating": 3.7,
                "votes": 100,
                "weighted_rating": 3.7,
            }
            for i in range(10)
        ]
    )
    orders = {tuple(rank(score(df.sample(frac=1, random_state=seed)))["id"]) for seed in range(5)}
    assert len(orders) == 1, "ranking order changed when input order changed"


def test_tie_break_prefers_more_votes():
    df = make_df(
        [
            {"id": "few", "votes": 10, "weighted_rating": 4.0},
            {"id": "many", "votes": 5000, "weighted_rating": 4.0},
        ]
    )
    assert list(rank(score(df))["id"]) == ["many", "few"]


def test_unrated_sorts_last_but_is_not_scored_zero():
    """4.7 — unrated is 'unknown', not 'terrible'; it must still lose to a real 2.0."""
    df = make_df(
        [
            {"id": "unrated", "rating": np.nan, "weighted_rating": np.nan},
            {"id": "poor", "rating": 2.0, "weighted_rating": 2.0},
        ]
    )
    ranked = rank(score(df))
    assert list(ranked["id"]) == ["poor", "unrated"]
    assert ranked.loc[ranked["id"] == "unrated", "score"].iloc[0] < 0


# ------------------------------------------------------------------ soft preferences


def test_soft_boost_rewards_family_friendly_when_asked():
    row = make_df([{"is_family_friendly": True}]).iloc[0]
    assert soft_preference_boost(row, "somewhere family friendly") > 0


def test_soft_boost_ignores_flag_when_not_asked():
    row = make_df([{"is_family_friendly": True}]).iloc[0]
    assert soft_preference_boost(row, "quiet and cheap") == 0.0


def test_soft_boost_empty_extras_is_zero():
    row = make_df([{"is_family_friendly": True}]).iloc[0]
    assert soft_preference_boost(row, "") == 0.0
    assert soft_preference_boost(row, "   ") == 0.0


def test_soft_boost_matches_dish_names():
    row = make_df([{"dish_liked": ["Biryani", "Kebab"]}]).iloc[0]
    assert soft_preference_boost(row, "craving biryani") > 0


def test_soft_boost_never_penalises_missing_optional_data():
    """4.9 — dish_liked is empty for 63% of rows; that must cost nothing."""
    row = make_df([{"dish_liked": []}]).iloc[0]
    assert soft_preference_boost(row, "craving biryani") == 0.0


def test_soft_boost_handles_gibberish_and_non_english():
    """4.10 — no keyword match, no crash."""
    row = make_df([{}]).iloc[0]
    assert soft_preference_boost(row, "asdkjh ???? 🍕 中文") == 0.0


def test_boost_cannot_lift_unrated_above_rated():
    df = make_df(
        [
            {
                "id": "unrated",
                "rating": np.nan,
                "weighted_rating": np.nan,
                "is_family_friendly": True,
            },
            {"id": "rated", "rating": 2.0, "weighted_rating": 2.0},
        ]
    )
    assert list(rank(score(df, "family friendly"))["id"]) == ["rated", "unrated"]


# --------------------------------------------------------------------- brand capping


def test_cap_per_brand_limits_chain_outlets():
    """4.3 — Cafe Coffee Day has 54 outlets; five in a row reads as a broken product."""
    df = make_df([{"id": f"ccd{i}", "name": "Cafe Coffee Day"} for i in range(5)])
    assert len(cap_per_brand(df, limit=2)) == 2


def test_cap_per_brand_preserves_order_and_other_brands():
    df = make_df(
        [
            {"id": "a", "name": "Cafe Coffee Day"},
            {"id": "b", "name": "Toit"},
            {"id": "c", "name": "Cafe Coffee Day"},
            {"id": "d", "name": "Cafe Coffee Day"},
            {"id": "e", "name": "Truffles"},
        ]
    )
    assert list(cap_per_brand(df, limit=2)["id"]) == ["a", "b", "c", "e"]


def test_cap_per_brand_is_case_insensitive():
    df = make_df([{"id": "a", "name": "Toit"}, {"id": "b", "name": "TOIT"}])
    assert len(cap_per_brand(df, limit=1)) == 1


def test_cap_per_brand_empty_frame():
    assert cap_per_brand(make_df([]).iloc[0:0], limit=2).empty


# ----------------------------------------------------------------------- explanations


def test_explain_uses_only_real_fields():
    row = make_df(
        [{"rating": 4.4, "votes": 1200, "cost_for_two": 600, "cuisines": ["North Indian"]}]
    ).iloc[0]
    text = explain(row)
    assert "4.4" in text and "1,200" in text and "600" in text and "North Indian" in text


def test_explain_handles_unrated_without_saying_zero():
    """6.4 — must read as 'not yet rated', never 0 or nan."""
    row = make_df([{"rating": np.nan}]).iloc[0]
    text = explain(row)
    assert "not yet rated" in text.lower()
    assert "0.0" not in text and "nan" not in text.lower()


def test_explain_lists_requested_cuisine_first():
    """A correct match must not look wrong: display truncates to three cuisines, so the
    requested one has to lead. ECHOES Koramangala lists Italian fourth."""
    row = make_df(
        [{"cuisines": ["Chinese", "American", "Continental", "Italian", "North Indian"]}]
    ).iloc[0]
    assert "Italian" in explain(row, requested_cuisines=["Italian"])
    assert "Italian" not in explain(row)


def test_explain_omits_missing_cost():
    row = make_df([{"cost_for_two": np.nan}]).iloc[0]
    assert "₹" not in explain(row)


def test_highlights_are_factual_chips():
    row = make_df([{"rating": 4.2, "cost_for_two": 500, "budget_band": "medium"}]).iloc[0]
    chips = highlights(row)
    assert "4.2 rating" in chips
    assert "₹500 for two" in chips


# ------------------------------------------------------------------------ relaxation


@pytest.fixture
def pool() -> pd.DataFrame:
    """A pool where an over-constrained query needs relaxation to reach the floor."""
    rows = [
        {
            "id": "w1",
            "name": "A",
            "location": "Whitefield",
            "cuisines": ["Italian"],
            "rating": 4.6,
            "weighted_rating": 4.6,
            "budget_band": "high",
            "cost_for_two": 900,
        },
        {
            "id": "w2",
            "name": "B",
            "location": "Whitefield",
            "cuisines": ["Pizza"],
            "rating": 4.2,
            "weighted_rating": 4.2,
            "budget_band": "high",
            "cost_for_two": 800,
        },
        {
            "id": "w3",
            "name": "C",
            "location": "Whitefield",
            "cuisines": ["North Indian"],
            "rating": 3.9,
            "weighted_rating": 3.9,
            "budget_band": "medium",
            "cost_for_two": 400,
        },
        {
            "id": "w4",
            "name": "D",
            "location": "Whitefield",
            "cuisines": ["Chinese"],
            "rating": 3.5,
            "weighted_rating": 3.5,
            "budget_band": "low",
            "cost_for_two": 250,
        },
        {
            "id": "j1",
            "name": "E",
            "location": "Jayanagar",
            "cuisines": ["Biryani"],
            "rating": 4.4,
            "weighted_rating": 4.4,
            "budget_band": "medium",
            "cost_for_two": 450,
        },
        {
            "id": "j2",
            "name": "F",
            "location": "Jayanagar",
            "cuisines": ["Desserts"],
            "rating": 4.0,
            "weighted_rating": 4.0,
            "budget_band": "low",
            "cost_for_two": 200,
        },
        {
            "id": "k1",
            "name": "G",
            "location": "Jakkur",
            "cuisines": ["Vegan"],
            "rating": 3.4,
            "weighted_rating": 3.4,
            "budget_band": "low",
            "cost_for_two": 150,
        },
    ]
    return make_df(rows)


def test_relax_not_needed_when_enough_matches(pool):
    result = relax(pool, UserPreferences(location="Whitefield"), floor=4)
    assert len(result.df) == 4
    assert result.relaxations == []


def test_relax_triggers_below_floor_and_records_what_changed(pool):
    """The second property the plan names explicitly.

    The pool cannot actually reach 3 for this query, which is the point: relaxation must
    still fire, widen the result, and report every step for display.
    """
    prefs = UserPreferences(location="Whitefield", cuisines=["Italian"], min_rating=4.5)
    unrelaxed = relax(pool, prefs, floor=1)
    result = relax(pool, prefs, floor=3)
    assert result.was_relaxed
    assert len(result.df) > len(unrelaxed.df), "relaxation should widen the result set"
    assert all(r.detail for r in result.relaxations), "every relaxation needs display text"


def test_relax_stops_as_soon_as_the_floor_is_met(pool):
    """Relaxation is progressive: it must not keep loosening once it has enough."""
    prefs = UserPreferences(location="Whitefield", cuisines=["Italian"], min_rating=4.5)
    result = relax(pool, prefs, floor=2)
    assert len(result.df) >= 2
    # Reached the floor by widening cuisine and rating, so location was never touched.
    assert "location" not in result.relaxed_constraints


def test_relax_follows_the_documented_order(pool):
    """Meal context first, budget last."""
    prefs = UserPreferences(
        location="Whitefield",
        budget="high",
        cuisines=["Italian"],
        min_rating=4.5,
        meal_context="Buffet",
    )
    order = [r.constraint for r in relax(pool, prefs, floor=7).relaxations]
    assert order[0] == "meal_context"
    assert order[-1] == "budget"
    assert order.index("cuisine") < order.index("min_rating") < order.index("location")


def test_relax_terminates_on_impossible_query(pool):
    """4.5 — a 1-restaurant location plus a 1-restaurant cuisine cannot reach 5."""
    prefs = UserPreferences(location="Jakkur", cuisines=["Vegan"], min_rating=4.9)
    result = relax(pool, prefs, floor=5)
    assert len(result.df) < 5
    assert result.was_relaxed


def test_relax_only_reports_constraints_that_were_set(pool):
    """No phantom notices for filters the user never applied."""
    result = relax(pool, UserPreferences(min_rating=4.9), floor=5)
    assert result.relaxed_constraints <= {"min_rating"}


def test_relax_widens_cuisine_to_related_before_dropping(pool):
    prefs = UserPreferences(cuisines=["Italian"], min_rating=4.0)
    result = relax(pool, prefs, floor=2)
    assert "cuisine" in result.relaxed_constraints
    assert "pizza" in related_cuisines(["Italian"])


def test_related_cuisines_is_symmetric():
    assert "north indian" in related_cuisines(["Mughlai"])
    assert "mughlai" in related_cuisines(["North Indian"])


def test_related_cuisines_leaves_isolated_cuisine_alone():
    """A cuisine in no family cannot be widened, so relaxation must drop it instead."""
    assert related_cuisines(["Vegan"]) == {"vegan"}


def test_adjacent_locations_uses_name_derived_families():
    known = ["Koramangala 4th Block", "Koramangala 5th Block", "Whitefield", "BTM"]
    assert adjacent_locations("Koramangala 5th Block", known) == {
        "Koramangala 4th Block",
        "Koramangala 5th Block",
    }


def test_adjacent_locations_returns_empty_for_unrelated_neighborhood():
    """C2 — with no name-derived family, relaxation drops location rather than guessing."""
    assert adjacent_locations("Whitefield", ["Whitefield", "BTM", "Jayanagar"]) == set()


def test_strict_matches_rank_above_relaxed_ones(pool):
    """A user who asked for Jakkur must still see the Jakkur restaurant first.

    Before this, dropping the location constraint let higher-rated restaurants elsewhere
    push the user's only real match out of the results entirely.
    """
    prefs = UserPreferences(location="Jakkur")
    result = relax(pool, prefs, floor=5, known_locations=list(pool["location"].unique()))
    ranked = rank(score(result.df, strict_ids=result.strict_ids))
    assert ranked.iloc[0]["location"] == "Jakkur"
    assert len(ranked) >= 5, "relaxation should still fill out the list"


def test_relax_location_drops_rather_than_widening_via_city_areas(pool):
    """C2 — widening Whitefield via city_areas would return 60% of the dataset."""
    prefs = UserPreferences(location="Jakkur", min_rating=3.0)
    result = relax(pool, prefs, floor=5, known_locations=list(pool["location"].unique()))
    assert "location" in result.relaxed_constraints
    detail = next(r.detail for r in result.relaxations if r.constraint == "location")
    assert "all areas" in detail
