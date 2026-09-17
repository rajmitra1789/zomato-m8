"""Field-level parsers for the raw Zomato dataset.

Pure functions with no pandas dependency, so they are cheap to unit-test in isolation.
Every function is total: any input returns a value, none raise. A single malformed row
must never abort a 51,717-row ingest.

The tolerated inputs are not hypothetical — see docs/edge-case.md §1 for the observed
counts behind each one.
"""

import ast
import math
import re
import unicodedata

# Strings that mean "no value" across several columns. '-' appears in the `rate` column
# alongside 'NEW'; missing it produces either a crash or a bogus numeric.
_MISSING_STRINGS = frozenset({"", "-", "na", "n/a", "nan", "none", "null", "new"})

_RATING_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:/\s*5)?\s*$")
_COST_RE = re.compile(r"(\d+(?:\.\d+)?)")
_WHITESPACE_RE = re.compile(r"\s+")
_RATED_PREFIX_RE = re.compile(r"^\s*rated\b[\s:]*", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]+")
_TRUTHY = frozenset({"yes", "true", "1", "y"})

_MAX_ENCODING_PASSES = 8


def _is_missing(value: object) -> bool:
    """True for None and pandas' NaN, which arrives as a float for missing object cells."""
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


def _as_text(value: object) -> str | None:
    """Coerce to a stripped string, or None when the value carries no information."""
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def fix_encoding(value: object) -> str | None:
    """Repair UTF-8-decoded-as-latin-1 mojibake and collapse whitespace.

    The corruption in this dataset is nested: 'Café' appears as a ~16-character run of
    'Ã\\x83Â\\x83...' and needs five successive round-trips to unwind, so a single
    encode/decode pass leaves the name still broken. Iterate until the text stops
    changing or the round-trip stops being valid, keeping the last good result.
    """
    text = _as_text(value)
    if text is None:
        return None

    for _ in range(_MAX_ENCODING_PASSES):
        try:
            candidate = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == text:
            break
        text = candidate

    # Drop control characters, which survive the round-trip and break Parquet round-tripping.
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return _WHITESPACE_RE.sub(" ", text).strip()


def parse_rating(value: object) -> float | None:
    """'4.1/5' and '4.1 /5' -> 4.1; 'NEW', '-', null, and junk -> None.

    Never returns 0.0 for a missing rating: that would sort an unrated restaurant below
    a genuine 1.8 rather than flagging it as unknown.
    """
    text = _as_text(value)
    if text is None or text.lower() in _MISSING_STRINGS:
        return None

    match = _RATING_RE.match(text)
    if match is None:
        return None

    rating = float(match.group(1))
    return rating if 0.0 <= rating <= 5.0 else None


def parse_cost(value: object) -> int | None:
    """'1,200' -> 1200. Missing stays None, never 0, which would pass as free."""
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None

    text = str(value).strip()
    if not text or text.lower() in _MISSING_STRINGS:
        return None
    if text.lstrip().startswith("-"):
        return None

    match = _COST_RE.search(text.replace(",", ""))
    if match is None:
        return None
    return int(float(match.group(1)))


def parse_yes_no(value: object) -> bool:
    """'Yes' -> True. Anything unrecognised is False rather than an error."""
    if _is_missing(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def split_multi(value: object) -> list[str]:
    """'North Indian, Chinese' -> ['North Indian', 'Chinese'].

    Returns [] for missing input rather than [''], which would otherwise appear as a
    blank entry in the cuisine dropdown. Deduplicates case-insensitively while keeping
    first-seen order and original casing.
    """
    text = fix_encoding(value)
    if text is None:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def canonical_url(value: object) -> str | None:
    """Strip the query string and fragment to get a stable per-restaurant URL.

    Every one of the 51,717 raw URLs is unique because of a '?context=<base64>' tracking
    parameter, so deduplicating on the raw column silently removes nothing. This is the
    function that makes dedup work.
    """
    text = _as_text(value)
    if text is None:
        return None
    path = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return path or None


def normalize_name(value: object) -> str:
    """Casefolded, punctuation-free, whitespace-collapsed name for dedup comparisons.

    Short names ('HQ', 'F5', 'B1') are real and must survive untouched.
    """
    text = fix_encoding(value)
    if text is None:
        return ""
    text = _PUNCT_RE.sub("", text.casefold())
    return _WHITESPACE_RE.sub(" ", text).strip()


def dedup_key(url: object, name: object, address: object) -> str:
    """Stable identity for one physical restaurant.

    Prefers the canonical URL; falls back to normalized name plus address for the rows
    where it is missing. Address is part of the fallback because a brand can have several
    outlets in one neighborhood — 328 (name, location) pairs do.
    """
    canonical = canonical_url(url)
    if canonical:
        return canonical
    return f"{normalize_name(name)}|{normalize_name(address)}"


def extract_review_snippets(
    value: object,
    max_snippets: int = 2,
    max_chars: int = 200,
) -> list[str]:
    """Pull a couple of short review snippets from the stringified list-of-tuples column.

    `reviews_list` dominates the dataset's ~575 MB memory footprint, so ingestion keeps
    only a truncated sample. The raw text is arbitrary user content from the internet:
    parse it with `literal_eval` (never `eval`) and swallow every failure, because one
    unparseable row must not abort the ingest.
    """
    text = _as_text(value)
    if text is None:
        return []

    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return []

    if not isinstance(parsed, (list, tuple)):
        return []

    out: list[str] = []
    for entry in parsed:
        if len(out) >= max_snippets:
            break

        # Entries are ('Rated 4.0', 'RATED\n  text'); tolerate anything else.
        if isinstance(entry, (list, tuple)):
            body = entry[-1] if entry else None
        elif isinstance(entry, str):
            body = entry
        else:
            continue

        if not isinstance(body, str):
            continue

        snippet = fix_encoding(body)
        if snippet is None:
            continue
        snippet = _RATED_PREFIX_RE.sub("", snippet).strip()
        if not snippet:
            continue

        if len(snippet) > max_chars:
            # Slicing operates on characters, so this cannot split a multibyte sequence.
            snippet = snippet[:max_chars].rstrip() + "\u2026"
        out.append(snippet)

    return out
