"""Deterministic scoring: soft-preference boosts, stable ordering, and brand diversity.

`weighted_rating` is precomputed during ingestion (vote-shrunk toward the global mean).
This module adds small boosts for free-text preferences on top of it, then produces a
stable ordering. The LLM later reranks; nothing here tries to be clever about semantics.
"""

import re
from collections.abc import Iterable

import pandas as pd

from zomato_reco.config import settings

# Keyword vocabularies for the free-text "extras" box. Deliberately small and explicit:
# nuanced interpretation is the LLM's job, and a boost that fires on the wrong word is
# worse than no boost at all.
_FAMILY_KEYWORDS = frozenset(
    {"family", "families", "kids", "child", "children", "group", "groups", "parents"}
)
_QUICK_KEYWORDS = frozenset(
    {"quick", "fast", "speedy", "takeaway", "take-away", "grab", "hurry", "express"}
)
_DELIVERY_KEYWORDS = frozenset({"delivery", "deliver", "order", "online", "home"})
_BOOKING_KEYWORDS = frozenset({"book", "booking", "reserve", "reservation", "table"})

_WORD_RE = re.compile(r"[a-z0-9\-]+")

# Unrated restaurants sort below every rated one. Using a sentinel keeps the sort stable
# without pretending an unknown rating is a bad rating.
_UNRATED_SENTINEL = -1.0


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.casefold()))


def _as_list(cell: Iterable[str] | None) -> list[str]:
    """Strings from a multi-value cell.

    Never use `cell or []` on these: Parquet hands back numpy arrays, and their truth value
    raises ValueError for any size other than 1.
    """
    if cell is None:
        return []
    return [v for v in cell if isinstance(v, str) and v.strip()]


def _tokens(cell: Iterable[str] | None) -> set[str]:
    """All words appearing across a multi-value cell, for loose keyword matching."""
    if cell is None:
        return set()
    out: set[str] = set()
    for value in cell:
        if isinstance(value, str):
            out |= _words(value)
    return out


def soft_preference_boost(row: pd.Series, extras: str) -> float:
    """Small additive boost reflecting the free-text preferences.

    Only reorders candidates; never excludes. `dish_liked` is empty for 63% of rows, so
    the absence of a keyword match must cost a restaurant nothing.
    """
    if not extras or not extras.strip():
        return 0.0

    asked = _words(extras)
    if not asked:
        return 0.0

    boost = 0.0

    if asked & _FAMILY_KEYWORDS and bool(row.get("is_family_friendly")):
        boost += settings.weight_family_friendly
    if asked & _QUICK_KEYWORDS and bool(row.get("is_quick_service")):
        boost += settings.weight_quick_service
    if asked & _DELIVERY_KEYWORDS and bool(row.get("online_order")):
        boost += settings.weight_online_order
    if asked & _BOOKING_KEYWORDS and bool(row.get("book_table")):
        boost += settings.weight_book_table

    # Direct hits on dish names or restaurant types the user described.
    if asked & _tokens(row.get("dish_liked")):
        boost += settings.weight_dish_match
    if asked & _tokens(row.get("rest_types")):
        boost += settings.weight_rest_type_match

    return boost


def score(df: pd.DataFrame, extras: str = "", strict_ids: set[str] | None = None) -> pd.DataFrame:
    """Attach `score` and the sort keys used for ranking. Returns a new frame.

    `strict_ids` names the rows that matched the user's original request. They rank above
    anything admitted by relaxation, so a widened search still leads with what was asked for.
    """
    out = df.copy()

    if strict_ids:
        out["_strict"] = out["id"].isin(strict_ids)
    else:
        out["_strict"] = False

    base = pd.to_numeric(out["weighted_rating"], errors="coerce")
    out["_has_rating"] = base.notna()
    out["_base_score"] = base.fillna(_UNRATED_SENTINEL)

    if extras and extras.strip():
        out["_boost"] = [soft_preference_boost(row, extras) for _, row in out.iterrows()]
    else:
        out["_boost"] = 0.0

    # An unrated restaurant should not climb above a rated one on boosts alone.
    out["score"] = out["_base_score"] + out["_boost"].where(out["_has_rating"], 0.0)
    return out


def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by score with a fully deterministic tie-break.

    147 restaurants share an identical (rating, cost) pair, so score ties are common. Votes
    break most of them and `id` breaks the rest, which guarantees the same input always
    produces the same order — a property the eval suite asserts across runs.
    """
    keys = ["_strict", "_has_rating", "score", "votes", "id"]
    keys = [k for k in keys if k in df.columns]
    return df.sort_values(
        by=keys,
        ascending=[k == "id" for k in keys],
        kind="mergesort",
    ).reset_index(drop=True)


def cap_per_brand(df: pd.DataFrame, limit: int | None = None) -> pd.DataFrame:
    """Limit how many outlets of one brand can appear, preserving rank order.

    Without this, a broad search legitimately returns five Cafe Coffee Days — the brand has
    54 outlets — which reads as a broken product rather than a helpful list.
    """
    limit = settings.max_per_brand if limit is None else limit
    if limit <= 0 or df.empty:
        return df

    seen: dict[str, int] = {}
    keep: list[int] = []
    for pos, name in enumerate(df["name"]):
        key = str(name).strip().casefold()
        count = seen.get(key, 0)
        if count < limit:
            seen[key] = count + 1
            keep.append(pos)
    return df.iloc[keep].reset_index(drop=True)


def _ordered_cuisines(cell: Iterable[str] | None, requested: Iterable[str] = ()) -> list[str]:
    """Cuisines with the user's requested ones first.

    Restaurants list up to six cuisines and display truncates to three, so without this a
    correct match can hide the very cuisine that was asked for — ECHOES Koramangala lists
    Italian fourth, which made an Italian search look broken.
    """
    cuisines = _as_list(cell)
    wanted = {c.strip().casefold() for c in requested if c and c.strip()}
    if not wanted:
        return cuisines
    matched = [c for c in cuisines if c.casefold() in wanted]
    rest = [c for c in cuisines if c.casefold() not in wanted]
    return matched + rest


def explain(row: pd.Series, requested_cuisines: Iterable[str] = ()) -> str:
    """Template explanation over real fields, used whenever the LLM is unavailable.

    Phase 5's fallback path depends on this, so it must read acceptably on its own and must
    never invent anything: every clause is a dataset field or is omitted.
    """
    parts: list[str] = []

    rating = row.get("rating")
    votes = int(row.get("votes") or 0)
    if pd.notna(rating):
        if votes:
            parts.append(f"rated {rating:.1f}/5 from {votes:,} votes")
        else:
            parts.append(f"rated {rating:.1f}/5")
    else:
        parts.append("not yet rated")

    cost = row.get("cost_for_two")
    if pd.notna(cost):
        parts.append(f"about ₹{int(cost):,} for two")

    # `or []` would raise here: these cells hold numpy arrays, whose truth value is
    # ambiguous for any size other than 1.
    cuisines = _ordered_cuisines(row.get("cuisines"), requested_cuisines)
    if cuisines:
        parts.append("serves " + ", ".join(cuisines[:3]))

    perks = []
    if bool(row.get("online_order")):
        perks.append("online ordering")
    if bool(row.get("book_table")):
        perks.append("table booking")
    if perks:
        parts.append(" and ".join(perks) + " available")

    text = "; ".join(parts)
    # Not str.capitalize(): it lowercases everything after the first character, which would
    # turn "North Indian" into "north indian".
    return text[:1].upper() + text[1:] + "."


def highlights(row: pd.Series) -> list[str]:
    """Short factual chips, assembled from dataset fields rather than model prose."""
    out: list[str] = []
    rating = row.get("rating")
    if pd.notna(rating):
        out.append(f"{rating:.1f} rating")
    cost = row.get("cost_for_two")
    if pd.notna(cost):
        out.append(f"₹{int(cost):,} for two")
    if row.get("budget_band"):
        out.append(f"{row['budget_band']} budget")
    if bool(row.get("is_family_friendly")):
        out.append("family friendly")
    if bool(row.get("is_quick_service")):
        out.append("quick service")
    return out
