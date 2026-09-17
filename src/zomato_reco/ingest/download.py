"""Fetch the raw dataset, cached locally so reruns work offline.

`menu_item` and `phone` are never read: the first is almost always empty and the second is
mild PII with no use here. Not loading them is the cheapest form of dropping them.
"""

import logging
from pathlib import Path

import pandas as pd

from zomato_reco.config import settings

logger = logging.getLogger(__name__)

# Every column the pipeline needs, in dataset order.
RAW_COLUMNS = [
    "url",
    "address",
    "name",
    "online_order",
    "book_table",
    "rate",
    "votes",
    "location",
    "rest_type",
    "dish_liked",
    "cuisines",
    "approx_cost(for two people)",
    "reviews_list",
    "listed_in(type)",
    "listed_in(city)",
]

CACHE_FILENAME = "zomato_raw.parquet"


class DatasetSchemaError(RuntimeError):
    """The upstream dataset no longer looks like what this pipeline expects."""


def _cached_parquet_files(raw_dir: Path) -> list[Path]:
    """Any Parquet in the raw directory counts as a cache, including HF shard downloads."""
    return sorted(p for p in raw_dir.glob("*.parquet") if p.is_file())


def _validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise DatasetSchemaError(
            f"Dataset is missing expected columns: {missing}. "
            f"The upstream dataset ({settings.hf_dataset_id}) may have changed schema."
        )


def load_raw_dataframe(force_download: bool = False) -> pd.DataFrame:
    """Return the raw dataset as a DataFrame, preferring the local cache.

    Reads only RAW_COLUMNS. The download is ~150 MB, so the cache matters: after the first
    run, ingestion needs no network at all.
    """
    raw_dir = settings.raw_data_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not force_download:
        cached = _cached_parquet_files(raw_dir)
        if cached:
            logger.info("Reading cached Parquet: %s", ", ".join(p.name for p in cached))
            frames = [pd.read_parquet(p, columns=RAW_COLUMNS) for p in cached]
            df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            _validate_columns(df)
            return df

    logger.info("Downloading %s (split=%s)", settings.hf_dataset_id, settings.hf_split)
    from datasets import load_dataset  # imported lazily: only needed on a cache miss

    dataset = load_dataset(settings.hf_dataset_id, split=settings.hf_split)
    df = dataset.to_pandas()
    _validate_columns(df)
    df = df[RAW_COLUMNS]

    cache_path = raw_dir / CACHE_FILENAME
    df.to_parquet(cache_path, index=False, compression="snappy")
    logger.info("Cached raw dataset to %s", cache_path)
    return df
