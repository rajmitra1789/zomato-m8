"""Build the cleaned artifact: raw dataset -> restaurants.parquet + facets.json.

Run as: python -m zomato_reco.ingest.build_artifact

The pipeline follows architecture §6.1. Its most important property is the deduplication
assertion: 51,717 raw rows must collapse to ~12,453 restaurants. Deduplicating on the raw
`url` column looks like it works and removes nothing, so the count is checked rather than
trusted.
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from zomato_reco.config import settings
from zomato_reco.ingest.clean import (
    canonical_url,
    dedup_key,
    extract_review_snippets,
    fix_encoding,
    parse_cost,
    parse_rating,
    parse_yes_no,
    split_multi,
)
from zomato_reco.ingest.download import load_raw_dataframe

logger = logging.getLogger(__name__)

# Bumped when the cleaning logic changes, so a stale artifact can be detected.
ARTIFACT_VERSION = 1

# Known-good targets measured from the source dataset. A mismatch means a pipeline bug,
# not a data change — see docs/eval.md §1.1.
EXPECTED_RAW_ROWS = 51_717
EXPECTED_DEDUPED_ROWS = 12_453
EXPECTED_COST_P33 = 300.0
EXPECTED_COST_P66 = 500.0
EXPECTED_MEAN_RATING = 3.625
EXPECTED_LOCATIONS = 93
EXPECTED_CITY_AREAS = 30
EXPECTED_MEAL_CONTEXTS = 7
ROW_COUNT_TOLERANCE = 0.01

# Heuristic flags derived from the 25 atomic `rest_type` values present in the data.
# These are approximations of intent, not ground truth, which is why they only feed
# small scoring boosts rather than hard filters.
_FAMILY_TYPES = frozenset(
    {"Casual Dining", "Fine Dining", "Food Court", "Dhaba", "Bhojanalya", "Mess"}
)
_ALCOHOL_TYPES = frozenset({"Bar", "Pub", "Club", "Microbrewery", "Lounge"})
_QUICK_TYPES = frozenset(
    {"Quick Bites", "Takeaway", "Delivery", "Kiosk", "Food Truck", "Food Court", "Beverage Shop"}
)


class ArtifactValidationError(RuntimeError):
    """One or more post-build invariants failed."""


def _make_id(key: str) -> str:
    """Stable short hash of the dedup key.

    Must be derived from content, never a row index: the id has to survive a rebuild so
    cached LLM responses and snapshot tests stay valid.
    """
    return hashlib.blake2s(key.encode("utf-8"), digest_size=6).hexdigest()


def _weighted_rating(rating: float | None, votes: int, mean_rating: float, m: int) -> float | None:
    """IMDb-style Bayesian shrinkage toward the global mean.

    Returns None for unrated restaurants rather than 0.0. Zero would be a factual claim
    ("this place is terrible") when the truth is "unknown"; Phase 4 sorts None last
    explicitly instead.
    """
    if rating is None or pd.isna(rating):
        return None
    v = max(int(votes), 0)
    return (v / (v + m)) * rating + (m / (v + m)) * mean_rating


def _budget_band(cost: int | None, p33: float, p66: float) -> str | None:
    """Bands from measured percentiles, not hand-picked thresholds.

    Cost has only 70 discrete values with 1,921 restaurants sitting exactly on p33, so the
    bands cannot be even thirds. Boundaries are inclusive-low by choice and tested.
    """
    if cost is None or pd.isna(cost):
        return None
    if cost <= p33:
        return "low"
    if cost <= p66:
        return "medium"
    return "high"


def _parse_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the Phase 1 parsers column by column."""
    out = pd.DataFrame(index=df.index)
    out["name"] = df["name"].map(fix_encoding)
    out["address"] = df["address"].map(fix_encoding)
    out["location"] = df["location"].map(fix_encoding)
    out["city_area"] = df["listed_in(city)"].map(fix_encoding)
    out["meal_context"] = df["listed_in(type)"].map(fix_encoding)
    out["cuisines"] = df["cuisines"].map(split_multi)
    out["rest_types"] = df["rest_type"].map(split_multi)
    out["dish_liked"] = df["dish_liked"].map(split_multi)
    out["rating"] = df["rate"].map(parse_rating)
    out["votes"] = pd.to_numeric(df["votes"], errors="coerce").fillna(0).astype("int64")
    out["cost_for_two"] = df["approx_cost(for two people)"].map(parse_cost)
    out["online_order"] = df["online_order"].map(parse_yes_no)
    out["book_table"] = df["book_table"].map(parse_yes_no)
    out["url"] = df["url"].map(canonical_url)
    return out


def _aggregate_siblings(df: pd.DataFrame) -> pd.DataFrame:
    """Collect the listing dimensions of duplicate rows before collapsing them.

    A restaurant is listed once per meal type x search area, appearing in up to 14 areas.
    Keeping only the surviving row's single value would silently discard the rest.
    """
    grouped = df.groupby("dedup_key", sort=False)
    return pd.DataFrame(
        {
            "meal_contexts": grouped["meal_context"].apply(
                lambda s: sorted({v for v in s if isinstance(v, str) and v})
            ),
            "city_areas": grouped["city_area"].apply(
                lambda s: sorted({v for v in s if isinstance(v, str) and v})
            ),
            "listing_count": grouped.size(),
        }
    )


def _flags(rest_types: list[str], meal_contexts: list[str]) -> tuple[bool, bool]:
    types = set(rest_types)
    contexts = set(meal_contexts)
    family = bool(types & _FAMILY_TYPES or "Buffet" in contexts) and not (types & _ALCOHOL_TYPES)
    quick = bool(types & _QUICK_TYPES or "Delivery" in contexts)
    return family, quick


def build(strict: bool = True) -> dict:
    """Run the full ingestion pipeline and write the artifact. Returns a summary dict."""
    started = time.perf_counter()

    raw = load_raw_dataframe()
    raw_rows = len(raw)
    logger.info("Loaded %s raw rows", f"{raw_rows:,}")

    df = _parse_columns(raw)

    # Dedup key from the canonical URL, falling back to name + address.
    df["dedup_key"] = [
        dedup_key(u, n, a) for u, n, a in zip(raw["url"], raw["name"], raw["address"], strict=False)
    ]

    siblings = _aggregate_siblings(df)

    # Keep the best-evidenced row per restaurant, then attach the aggregated siblings.
    keep_idx = df.groupby("dedup_key", sort=False)["votes"].idxmax()
    deduped = df.loc[keep_idx].copy()
    deduped = deduped.join(siblings, on="dedup_key")

    # reviews_list is the single heaviest column, so snippets are extracted only for rows
    # that survived deduplication, then the raw text is released immediately.
    deduped["review_snippets"] = raw.loc[deduped.index, "reviews_list"].map(extract_review_snippets)
    del raw

    deduped = deduped.drop(columns=["meal_context", "city_area"])
    deduped_rows = len(deduped)
    logger.info("Deduplicated to %s restaurants", f"{deduped_rows:,}")

    # Drop unusable rows: no name, or neither a rating nor a cost to reason about.
    has_name = deduped["name"].notna() & (deduped["name"].astype("string").str.len() > 0)
    has_signal = deduped["rating"].notna() | deduped["cost_for_two"].notna()
    dropped_no_name = int((~has_name).sum())
    dropped_no_signal = int((has_name & ~has_signal).sum())
    deduped = deduped[has_name & has_signal].copy()

    # Derived columns. Percentiles and the mean come from the deduplicated distribution,
    # so they describe restaurants rather than listings.
    costs = pd.to_numeric(deduped["cost_for_two"], errors="coerce")
    p33 = float(costs.quantile(0.33))
    p66 = float(costs.quantile(0.66))
    mean_rating = float(pd.to_numeric(deduped["rating"], errors="coerce").mean())

    deduped["id"] = deduped["dedup_key"].map(_make_id)
    deduped["budget_band"] = [_budget_band(c, p33, p66) for c in deduped["cost_for_two"]]
    deduped["weighted_rating"] = [
        _weighted_rating(r, v, mean_rating, settings.min_votes_m)
        for r, v in zip(deduped["rating"], deduped["votes"], strict=True)
    ]
    flags = [
        _flags(rt, mc)
        for rt, mc in zip(deduped["rest_types"], deduped["meal_contexts"], strict=True)
    ]
    deduped["is_family_friendly"] = [f for f, _ in flags]
    deduped["is_quick_service"] = [q for _, q in flags]

    deduped = deduped.drop(columns=["dedup_key"]).sort_values("id").reset_index(drop=True)

    facets = _build_facets(deduped, p33=p33, p66=p66, mean_rating=mean_rating)

    # Write to temporary paths first: a build that fails validation must not replace a
    # known-good artifact with a broken one.
    tmp_artifact, tmp_facets = _write_outputs(deduped, facets, staged=True)

    summary = {
        "raw_rows": raw_rows,
        "deduped_rows": deduped_rows,
        "final_rows": len(deduped),
        "dropped_no_name": dropped_no_name,
        "dropped_no_signal": dropped_no_signal,
        "cost_p33": p33,
        "cost_p66": p66,
        "mean_rating": round(mean_rating, 3),
        "rating_min": float(deduped["rating"].min()),
        "rating_max": float(deduped["rating"].max()),
        "unrated_pct": round(deduped["rating"].isna().mean() * 100, 1),
        "locations": len(facets["locations"]),
        "city_areas": len(facets["city_areas"]),
        "cuisines": len(facets["cuisines"]),
        "meal_contexts": len(facets["meal_contexts"]),
        "unique_ids": int(deduped["id"].nunique()),
        "artifact_mb": round(tmp_artifact.stat().st_size / 1_048_576, 2),
        "null_counts": {
            col: int(deduped[col].isna().sum())
            for col in ("location", "rating", "cost_for_two", "budget_band", "weighted_rating")
        },
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }

    failures = _validate(summary, deduped)
    summary["validation_failures"] = failures
    if failures and strict:
        tmp_artifact.unlink(missing_ok=True)
        tmp_facets.unlink(missing_ok=True)
        raise ArtifactValidationError(
            "Artifact failed validation (existing artifact left untouched):\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    tmp_artifact.replace(settings.artifact_path)
    tmp_facets.replace(settings.facets_path)
    logger.info("Wrote %s and %s", settings.artifact_path, settings.facets_path)
    return summary


def _build_facets(df: pd.DataFrame, *, p33: float, p66: float, mean_rating: float) -> dict:
    """Dropdown values and calibrated thresholds, so the UI can only offer real queries."""

    def flatten(column: str) -> list[str]:
        return sorted({v for row in df[column] for v in row})

    return {
        "artifact_version": ARTIFACT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "restaurant_count": len(df),
        "locations": sorted({v for v in df["location"] if isinstance(v, str) and v}),
        "city_areas": flatten("city_areas"),
        "cuisines": flatten("cuisines"),
        "rest_types": flatten("rest_types"),
        "meal_contexts": flatten("meal_contexts"),
        # Boundaries are inclusive-low (cost <= p33 is "low"), so the labels must not
        # imply ₹300 belongs to both low and medium.
        "budget_bands": {
            "p33": p33,
            "p66": p66,
            "low": {"max": p33, "label": f"up to ₹{int(p33):,} for two"},
            "medium": {
                "min": p33 + 1,
                "max": p66,
                "label": f"₹{int(p33) + 1:,}–₹{int(p66):,} for two",
            },
            "high": {"min": p66 + 1, "label": f"over ₹{int(p66):,} for two"},
        },
        "rating": {
            "min": float(df["rating"].min()),
            "max": float(df["rating"].max()),
            "global_mean": round(mean_rating, 4),
            "unrated_count": int(df["rating"].isna().sum()),
        },
        "cost": {
            "min": int(pd.to_numeric(df["cost_for_two"], errors="coerce").min()),
            "max": int(pd.to_numeric(df["cost_for_two"], errors="coerce").max()),
        },
        "min_votes_m": settings.min_votes_m,
    }


def _write_outputs(df: pd.DataFrame, facets: dict, *, staged: bool) -> tuple[Path, Path]:
    """Write the artifact and facets, optionally to staging paths for an atomic swap."""
    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".tmp" if staged else ""
    artifact = settings.artifact_path.with_name(settings.artifact_path.name + suffix)
    facets_path = settings.facets_path.with_name(settings.facets_path.name + suffix)
    df.to_parquet(artifact, index=False, compression="snappy")
    facets_path.write_text(json.dumps(facets, indent=2, ensure_ascii=False) + "\n")
    return artifact, facets_path


def _validate(summary: dict, df: pd.DataFrame) -> list[str]:
    """Check the eval.md §1.1 invariants. Returns human-readable failures."""
    failures: list[str] = []

    def check_count(label: str, actual: int, expected: int) -> None:
        if abs(actual - expected) > expected * ROW_COUNT_TOLERANCE:
            failures.append(f"{label}: expected ~{expected:,}, got {actual:,}")

    check_count("raw rows", summary["raw_rows"], EXPECTED_RAW_ROWS)

    # The signature failure: dedup on the raw `url` leaves every row in place.
    if abs(summary["deduped_rows"] - EXPECTED_DEDUPED_ROWS) > (
        EXPECTED_DEDUPED_ROWS * ROW_COUNT_TOLERANCE
    ):
        hint = ""
        if summary["deduped_rows"] > EXPECTED_DEDUPED_ROWS * 2:
            hint = " — deduplication removed almost nothing; the ?context= param was not stripped"
        failures.append(
            f"deduped rows: expected ~{EXPECTED_DEDUPED_ROWS:,}, "
            f"got {summary['deduped_rows']:,}{hint}"
        )

    for label, actual, expected in (
        ("cost p33", summary["cost_p33"], EXPECTED_COST_P33),
        ("cost p66", summary["cost_p66"], EXPECTED_COST_P66),
    ):
        if actual != expected:
            failures.append(f"{label}: expected ₹{expected:,.0f}, got ₹{actual:,.0f}")

    if abs(summary["mean_rating"] - EXPECTED_MEAN_RATING) > 0.01:
        failures.append(
            f"global mean rating: expected {EXPECTED_MEAN_RATING}, got {summary['mean_rating']}"
        )

    if not (1.8 <= summary["rating_min"] and summary["rating_max"] <= 4.9):
        failures.append(
            f"rating range outside [1.8, 4.9]: "
            f"[{summary['rating_min']}, {summary['rating_max']}] — check parse_rating"
        )

    for label, actual, expected in (
        ("locations", summary["locations"], EXPECTED_LOCATIONS),
        ("city areas", summary["city_areas"], EXPECTED_CITY_AREAS),
        ("meal contexts", summary["meal_contexts"], EXPECTED_MEAL_CONTEXTS),
    ):
        if actual != expected:
            failures.append(f"distinct {label}: expected {expected}, got {actual}")

    if summary["unique_ids"] != summary["final_rows"]:
        failures.append(
            f"id collision: {summary['final_rows']:,} rows but {summary['unique_ids']:,} unique ids"
        )

    # A cost with no band means the banding logic skipped a row.
    orphan_bands = int((df["cost_for_two"].notna() & df["budget_band"].isna()).sum())
    if orphan_bands:
        failures.append(f"{orphan_bands} rows have a cost but no budget_band")

    # weighted_rating must be present exactly when a rating is.
    mismatch = int((df["rating"].notna() != df["weighted_rating"].notna()).sum())
    if mismatch:
        failures.append(f"{mismatch} rows disagree between rating and weighted_rating presence")

    if (df["rating"].dropna() == 0).any():
        failures.append("rating contains 0.0, which should be None for unrated restaurants")

    return failures


def _format_summary(summary: dict) -> str:
    lines = [
        "",
        "=" * 62,
        "  INGESTION SUMMARY",
        "=" * 62,
        f"  raw rows              {summary['raw_rows']:>12,}",
        f"  after deduplication   {summary['deduped_rows']:>12,}  (expected ~12,453)",
        f"  dropped: no name      {summary['dropped_no_name']:>12,}",
        f"  dropped: no signal    {summary['dropped_no_signal']:>12,}",
        f"  final rows            {summary['final_rows']:>12,}",
        "-" * 62,
        f"  cost p33 / p66        {'₹' + format(summary['cost_p33'], ',.0f'):>12}"
        f" / ₹{summary['cost_p66']:,.0f}",
        f"  global mean rating    {summary['mean_rating']:>12}",
        f"  rating range          {summary['rating_min']:>6} – {summary['rating_max']}",
        f"  unrated               {summary['unrated_pct']:>11}%",
        "-" * 62,
        f"  locations             {summary['locations']:>12}",
        f"  city areas            {summary['city_areas']:>12}",
        f"  cuisines              {summary['cuisines']:>12}",
        f"  meal contexts         {summary['meal_contexts']:>12}",
        "-" * 62,
        "  null counts:",
    ]
    lines.extend(f"    {col:<20}{count:>12,}" for col, count in summary["null_counts"].items())
    lines.extend(
        [
            "-" * 62,
            f"  artifact size         {summary['artifact_mb']:>11} MB",
            f"  elapsed               {summary['elapsed_seconds']:>11}s",
            "=" * 62,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the cleaned restaurant artifact.")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Report invariant failures without exiting non-zero (for debugging only).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show progress logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        summary = build(strict=not args.no_verify)
    except ArtifactValidationError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(_format_summary(summary))
    if summary["validation_failures"]:
        print("\nVALIDATION FAILURES:", file=sys.stderr)
        for failure in summary["validation_failures"]:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nAll invariants passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
