"""Hard constraint filters.

Each filter is a pure function taking a DataFrame and returning a narrower one, so they
compose in any order and are trivial to test. A filter given no value is a no-op, which is
what makes an empty preference form a valid query.

Two rules matter more than the rest:

* Multi-value columns are matched on **exact tokens**, never substrings. The vocabulary
  contains 'Indian' alongside 'North Indian', 'South Indian' and 'Modern Indian', and
  'Thai' alongside 'Mithai' — a `str.contains` here silently returns wrong restaurants.
* A minimum rating **excludes unrated restaurants** rather than comparing against NaN.
  That drops 24% of the catalogue, which is correct but worth surfacing in the UI.
"""

from collections.abc import Iterable, Sequence

import pandas as pd

from zomato_reco.models import UserPreferences

BUDGET_ORDER = ("low", "medium", "high")


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _token_set(values: Iterable[str] | None) -> set[str]:
    """Normalized token set from a cell holding a list or numpy array of strings."""
    if values is None:
        return set()
    return {_normalize(v) for v in values if isinstance(v, str) and v.strip()}


def _overlaps(cell: Iterable[str] | None, wanted: set[str]) -> bool:
    return bool(_token_set(cell) & wanted)


def filter_location(df: pd.DataFrame, location: str | None) -> pd.DataFrame:
    """Exact match on the restaurant's own neighborhood.

    Deliberately does not widen to `city_areas`: those are Zomato search-listing areas,
    not a parent region, and a restaurant appears in up to 14 of them. Widening would
    return most of the dataset. See docs/edge-case.md C2.
    """
    return filter_locations(df, [location] if location else None)


def filter_locations(df: pd.DataFrame, locations: Iterable[str] | None) -> pd.DataFrame:
    """Keep restaurants whose own neighborhood is in the given set.

    The set form exists for relaxation, which widens to a family of adjacent neighborhoods
    rather than to `city_areas`.
    """
    if not locations:
        return df
    wanted = {_normalize(loc) for loc in locations if loc and loc.strip()}
    if not wanted:
        return df
    return df[df["location"].map(lambda v: isinstance(v, str) and _normalize(v) in wanted)]


def filter_budget(df: pd.DataFrame, band: str | None, *, at_or_below: bool = False) -> pd.DataFrame:
    """Match the calibrated budget band.

    Restaurants with no cost have `budget_band` of None and are excluded rather than
    defaulted into "low", which would present an unknown price as cheap.
    """
    if not band:
        return df
    target = _normalize(band)
    if at_or_below and target in BUDGET_ORDER:
        return filter_budget_bands(df, BUDGET_ORDER[: BUDGET_ORDER.index(target) + 1])
    return filter_budget_bands(df, [target])


def filter_budget_bands(df: pd.DataFrame, bands: Iterable[str] | None) -> pd.DataFrame:
    """Keep restaurants in any of the given bands, used when relaxation widens the budget."""
    if not bands:
        return df
    wanted = {_normalize(b) for b in bands if b and b.strip()}
    if not wanted:
        return df
    return df[df["budget_band"].map(lambda v: isinstance(v, str) and _normalize(v) in wanted)]


def filter_min_rating(df: pd.DataFrame, min_rating: float | None) -> pd.DataFrame:
    """Keep restaurants rated at or above the threshold.

    Unrated restaurants are excluded explicitly. Relying on NaN comparisons happening to
    be False would work today but hides the intent, and the exclusion is a product
    decision worth stating: it removes ~3,000 restaurants.
    """
    if min_rating is None:
        return df
    return df[df["rating"].notna() & (df["rating"] >= min_rating)]


def filter_cuisines(df: pd.DataFrame, cuisines: Sequence[str] | None) -> pd.DataFrame:
    """Keep restaurants serving **any** requested cuisine.

    Any-overlap rather than all-of: requiring every selected cuisine on one restaurant is
    almost always empty, and users select multiple cuisines to broaden, not narrow.
    """
    if not cuisines:
        return df
    wanted = {_normalize(c) for c in cuisines if c and c.strip()}
    if not wanted:
        return df
    return df[df["cuisines"].map(lambda cell: _overlaps(cell, wanted))]


def filter_meal_context(df: pd.DataFrame, meal_context: str | None) -> pd.DataFrame:
    """Keep restaurants listed under the requested meal context (Buffet, Delivery, ...)."""
    if not meal_context:
        return df
    wanted = {_normalize(meal_context)}
    return df[df["meal_contexts"].map(lambda cell: _overlaps(cell, wanted))]


def apply_filters(
    df: pd.DataFrame, prefs: UserPreferences, *, budget_at_or_below: bool = False
) -> pd.DataFrame:
    """Apply every hard constraint in the preferences.

    Order is cheap-and-selective first: scalar column comparisons shrink the frame before
    the per-row set intersections run. Location is both the cheapest and by far the most
    selective (93 values over ~12k rows), so it leads; cuisine overlap is the most
    expensive, so it runs last on the smallest frame.
    """
    out = filter_location(df, prefs.location)
    out = filter_budget(out, prefs.budget, at_or_below=budget_at_or_below)
    out = filter_min_rating(out, prefs.min_rating)
    out = filter_meal_context(out, prefs.meal_context)
    out = filter_cuisines(out, prefs.cuisines)
    return out
