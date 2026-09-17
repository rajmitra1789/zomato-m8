"""Progressive constraint relaxation for over-constrained queries.

An empty result set is a bad answer, but silently ignoring what the user asked for is a
worse one. So relaxation happens in a fixed order, and every step is recorded as
structured data that the UI must display.

Order (architecture §7.2): meal context, cuisine, rating, location, then budget. Budget is
last because it is the constraint users are least willing to bend.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import pandas as pd

from zomato_reco.config import settings
from zomato_reco.models import Relaxation, UserPreferences
from zomato_reco.recommend.filters import (
    BUDGET_ORDER,
    filter_budget_bands,
    filter_cuisines,
    filter_locations,
    filter_meal_context,
    filter_min_rating,
)

# Cuisines that a diner asking for one would plausibly accept instead. Grounded in the
# dataset's 107-value vocabulary; membership is symmetric via the lookup below.
_CUISINE_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "north indian",
            "mughlai",
            "awadhi",
            "lucknowi",
            "rajasthani",
            "kashmiri",
            "bihari",
            "modern indian",
            "indian",
        }
    ),
    frozenset(
        {
            "south indian",
            "andhra",
            "chettinad",
            "kerala",
            "tamil",
            "hyderabadi",
            "mangalorean",
        }
    ),
    frozenset({"chinese", "cantonese", "asian", "pan asian", "thai", "momos", "tibetan"}),
    frozenset({"italian", "pizza", "continental", "european", "mediterranean"}),
    frozenset({"desserts", "ice cream", "bakery", "mithai", "paan", "bubble tea"}),
    frozenset({"cafe", "coffee", "tea", "beverages", "juices", "bakery", "desserts"}),
    frozenset(
        {
            "fast food",
            "burger",
            "sandwich",
            "rolls",
            "wraps",
            "street food",
            "hot dogs",
            "finger food",
            "momos",
        }
    ),
    frozenset({"biryani", "hyderabadi", "mughlai", "kebab", "andhra", "north indian"}),
    frozenset({"seafood", "konkan", "mangalorean", "goan", "malwani", "kerala"}),
    frozenset(
        {
            "middle eastern",
            "lebanese",
            "arabian",
            "turkish",
            "afghani",
            "afghan",
            "iranian",
            "kebab",
        }
    ),
    frozenset({"japanese", "sushi", "korean", "asian", "pan asian"}),
    frozenset({"mexican", "tex-mex", "american", "bbq"}),
    frozenset({"bbq", "grill", "kebab", "charcoal chicken", "roast chicken", "steak"}),
)

_RATING_STEP = 0.3
# Below the dataset's own minimum a threshold stops meaning anything, so drop it instead.
_RATING_FLOOR = 1.8

_BLOCK_SUFFIX_RE = re.compile(r"\s+(\d+(?:st|nd|rd|th)\s+)?(block|stage|phase)\b.*$", re.IGNORECASE)


def related_cuisines(cuisines: Iterable[str]) -> set[str]:
    """The requested cuisines plus their family members."""
    wanted = {c.strip().casefold() for c in cuisines if c and c.strip()}
    out = set(wanted)
    for family in _CUISINE_FAMILIES:
        if wanted & family:
            out |= family
    return out


def _location_base(location: str) -> str:
    """'Koramangala 5th Block' -> 'Koramangala'."""
    return _BLOCK_SUFFIX_RE.sub("", location).strip()


def adjacent_locations(location: str, known_locations: Iterable[str]) -> set[str]:
    """Neighborhoods objectively related to this one by name structure.

    Only name-derived families qualify — in practice the nine Koramangala blocks, which are
    adjacent by construction. Hand-drawing a geographic adjacency map for all 93
    neighborhoods would require local knowledge this pipeline does not have, and a wrong
    guess would quietly recommend restaurants across the city. Everything else therefore
    relaxes by dropping the location constraint with an explicit notice.
    """
    base = _location_base(location).casefold()
    if not base or base == location.strip().casefold():
        # No block/stage suffix, so there is no name-derived family to widen into.
        family = {loc for loc in known_locations if _location_base(loc).casefold() == base}
        return family if len(family) > 1 else set()
    return {loc for loc in known_locations if _location_base(loc).casefold() == base}


@dataclass
class RelaxationResult:
    """Filtered rows plus the record of what had to be loosened to get them."""

    df: pd.DataFrame
    relaxations: list[Relaxation] = field(default_factory=list)
    # Rows that satisfied the user's original request, before anything was loosened. These
    # must rank first: a user who asked for Jakkur and gets five restaurants from elsewhere
    # has been ignored, even with a notice explaining why.
    strict_ids: set[str] = field(default_factory=set)

    @property
    def was_relaxed(self) -> bool:
        return bool(self.relaxations)

    @property
    def relaxed_constraints(self) -> set[str]:
        """Which constraints were loosened, so callers can verify the rest still hold."""
        return {r.constraint for r in self.relaxations}


@dataclass
class _Constraints:
    """Effective constraints, which can hold widened sets that UserPreferences cannot."""

    locations: set[str] | None
    bands: set[str] | None
    min_rating: float | None
    cuisines: set[str] | None
    meal_context: str | None

    @classmethod
    def from_prefs(cls, prefs: UserPreferences) -> "_Constraints":
        return cls(
            locations={prefs.location} if prefs.location else None,
            bands={prefs.budget} if prefs.budget else None,
            min_rating=prefs.min_rating,
            cuisines={c.casefold() for c in prefs.cuisines} if prefs.cuisines else None,
            meal_context=prefs.meal_context,
        )

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Same order as `filters.apply_filters`: cheap and selective first."""
        out = filter_locations(df, self.locations)
        out = filter_budget_bands(out, self.bands)
        out = filter_min_rating(out, self.min_rating)
        out = filter_meal_context(out, self.meal_context)
        out = filter_cuisines(out, self.cuisines)
        return out


def relax(
    df: pd.DataFrame,
    prefs: UserPreferences,
    *,
    floor: int | None = None,
    known_locations: Iterable[str] | None = None,
) -> RelaxationResult:
    """Filter, loosening constraints in the fixed order until `floor` rows are found.

    Terminates once the chain is exhausted, returning whatever exists — a one-restaurant
    neighborhood plus a one-restaurant cuisine cannot yield five results by any relaxation,
    and looping forever is not an improvement on saying so.
    """
    floor = settings.relaxation_floor if floor is None else floor
    constraints = _Constraints.from_prefs(prefs)
    result = constraints.apply(df)
    strict_ids = set(result["id"]) if "id" in result.columns else set()
    if len(result) >= floor:
        return RelaxationResult(df=result, relaxations=[], strict_ids=strict_ids)

    applied: list[Relaxation] = []
    known = list(known_locations) if known_locations is not None else []

    steps = (_relax_meal_context, _relax_cuisine, _relax_rating, _relax_location, _relax_budget)
    for step in steps:
        note = step(constraints, known)
        if note is None:
            continue  # constraint was not set, so there is nothing to loosen
        applied.append(note)
        result = constraints.apply(df)
        if len(result) >= floor:
            break

    return RelaxationResult(df=result, relaxations=applied, strict_ids=strict_ids)


def _relax_meal_context(c: _Constraints, _known: list[str]) -> Relaxation | None:
    if not c.meal_context:
        return None
    dropped, c.meal_context = c.meal_context, None
    return Relaxation(
        constraint="meal_context",
        detail=f"No matches for {dropped.lower()}, so that requirement was dropped.",
    )


def _relax_cuisine(c: _Constraints, _known: list[str]) -> Relaxation | None:
    if not c.cuisines:
        return None
    widened = related_cuisines(c.cuisines)
    if widened == c.cuisines:
        original = sorted(c.cuisines)
        c.cuisines = None
        return Relaxation(
            constraint="cuisine",
            detail=f"Too few {', '.join(original)} options, so any cuisine is included.",
        )
    original = sorted(c.cuisines)
    c.cuisines = widened
    return Relaxation(
        constraint="cuisine",
        detail=f"Widened {', '.join(original)} to include similar cuisines.",
    )


def _relax_rating(c: _Constraints, _known: list[str]) -> Relaxation | None:
    if c.min_rating is None:
        return None
    lowered = round(c.min_rating - _RATING_STEP, 2)
    if lowered <= _RATING_FLOOR:
        previous, c.min_rating = c.min_rating, None
        return Relaxation(
            constraint="min_rating",
            detail=f"Dropped the {previous:.1f}+ rating requirement to find enough options.",
        )
    previous, c.min_rating = c.min_rating, lowered
    return Relaxation(
        constraint="min_rating",
        detail=f"Lowered the minimum rating from {previous:.1f} to {lowered:.1f}.",
    )


def _relax_location(c: _Constraints, known: list[str]) -> Relaxation | None:
    """Widen to a name-derived family if one exists, otherwise drop the constraint.

    Never widens via `city_areas`: see `adjacent_locations` and docs/edge-case.md C2.
    """
    if not c.locations:
        return None

    if len(c.locations) == 1:
        (only,) = tuple(c.locations)
        neighbors = adjacent_locations(only, known)
        if len(neighbors) > 1:
            c.locations = neighbors
            return Relaxation(
                constraint="location",
                detail=f"Widened {only} to nearby {_location_base(only)} areas.",
            )

    dropped = ", ".join(sorted(c.locations))
    c.locations = None
    return Relaxation(
        constraint="location",
        detail=f"Too few options in {dropped}, so results come from all areas.",
    )


def _relax_budget(c: _Constraints, _known: list[str]) -> Relaxation | None:
    """Widen to the neighbouring bands. Relaxed last, and never silently."""
    if not c.bands:
        return None

    indices = {BUDGET_ORDER.index(b) for b in c.bands if b in BUDGET_ORDER}
    if not indices:
        c.bands = None
        return Relaxation(constraint="budget", detail="Budget filter removed.")

    widened = set()
    for i in indices:
        widened |= {BUDGET_ORDER[j] for j in (i - 1, i, i + 1) if 0 <= j < len(BUDGET_ORDER)}
    if widened == c.bands:
        c.bands = None
        return Relaxation(constraint="budget", detail="Budget filter removed to find options.")

    original = ", ".join(sorted(c.bands))
    c.bands = widened
    return Relaxation(
        constraint="budget",
        detail=f"Widened the {original} budget to neighbouring price ranges.",
    )
