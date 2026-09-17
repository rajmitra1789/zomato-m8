"""Tests for the field parsers.

Case IDs in comments refer to docs/edge-case.md §1. Every input here was observed in the
real dataset unless marked as defensive.
"""

import math

import pytest

from zomato_reco.ingest.clean import (
    canonical_url,
    dedup_key,
    extract_review_snippets,
    fix_encoding,
    normalize_name,
    parse_cost,
    parse_rating,
    parse_yes_no,
    split_multi,
)

# --------------------------------------------------------------------------- rating


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4.1/5", 4.1),  # 1.3 canonical form
        ("4.1 /5", 4.1),  # 1.3 whitespace variant, both forms exist for nearly every value
        (" 3.0 / 5 ", 3.0),
        ("1.8/5", 1.8),  # dataset minimum
        ("4.9/5", 4.9),  # dataset maximum
        ("NEW", None),  # 1.2 2,208 rows
        ("new", None),
        ("-", None),  # 1.1 the value the architecture originally missed
        (None, None),  # 1.4 7,775 raw rows
        (float("nan"), None),  # pandas missing value
        ("", None),
        ("   ", None),
        ("garbage", None),  # defensive: must not raise
        ("5.5/5", None),  # defensive: out of range
        ("-1/5", None),  # defensive: negative
    ],
)
def test_parse_rating(raw, expected):
    assert parse_rating(raw) == expected


def test_parse_rating_never_returns_zero_for_missing():
    """0.0 would rank an unrated restaurant below a genuine 1.8. Must be None."""
    for raw in ("NEW", "-", None, ""):
        assert parse_rating(raw) is None


# ----------------------------------------------------------------------------- cost


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("800", 800),
        ("1,200", 1200),  # 1.5 thousands separator — the classic silent failure
        ("6,000", 6000),  # dataset maximum
        ("40", 40),  # 1.11 real cheap eatery, not an error
        ("₹1,200", 1200),
        ("Rs 500", 500),
        (800, 800),  # already numeric
        (800.0, 800),
        (None, None),  # 1.6
        (float("nan"), None),
        ("", None),
        ("-", None),
        ("abc", None),
        ("-100", None),
    ],
)
def test_parse_cost(raw, expected):
    assert parse_cost(raw) == expected


def test_parse_cost_missing_is_none_not_zero():
    """0 would read as 'free' and pass a low-budget filter."""
    assert parse_cost(None) is None
    assert parse_cost("") is None


# --------------------------------------------------------------------------- yes/no


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yes", True),
        ("yes", True),
        (" YES ", True),
        ("No", False),
        ("no", False),
        (None, False),  # 1.15 defensive
        (float("nan"), False),
        ("", False),
        ("maybe", False),
        (True, True),
        (False, False),
    ],
)
def test_parse_yes_no(raw, expected):
    assert parse_yes_no(raw) is expected


# ---------------------------------------------------------------------- multi-value


def test_split_multi_basic():
    assert split_multi("North Indian, Chinese") == ["North Indian", "Chinese"]


def test_split_multi_trims_and_drops_empties():
    assert split_multi(" North Indian ,, Chinese , ") == ["North Indian", "Chinese"]


@pytest.mark.parametrize("raw", [None, "", "   ", ",", float("nan")])
def test_split_multi_missing_returns_empty_list(raw):
    """1.7, 1.8 — must be [] and never [''], which would create a blank facet value."""
    assert split_multi(raw) == []


def test_split_multi_deduplicates_case_insensitively():
    assert split_multi("Cafe, cafe, CAFE") == ["Cafe"]


def test_split_multi_preserves_order():
    assert split_multi("Desserts, Bakery, Cafe") == ["Desserts", "Bakery", "Cafe"]


def test_split_multi_repairs_encoding():
    assert split_multi("CafÃ\x83Â©, Bakery") == ["Café", "Bakery"]


# ------------------------------------------------------------------------- encoding


def test_fix_encoding_repairs_multi_layer_mojibake():
    """1.10 — the corruption is nested ~5 layers deep; one round-trip is not enough."""
    raw = (
        "CafÃ\x83Â\x83Ã\x82Â\x83Ã\x83Â\x82Ã\x82Â\x83Ã\x83Â\x83Ã\x82Â\x82Ã\x83Â\x82Ã\x82Â©"
        " Down The Alley"
    )
    assert fix_encoding(raw) == "Café Down The Alley"


def test_fix_encoding_single_layer():
    assert fix_encoding("CafÃ©") == "Café"


def test_fix_encoding_leaves_clean_text_untouched():
    for text in ("Jalsa", "Truffles", "Café Noir", "San José", "北京", "Bakery & Co."):
        assert fix_encoding(text) == text


def test_fix_encoding_collapses_whitespace():
    assert fix_encoding("  Jalsa   Restaurant  ") == "Jalsa Restaurant"


@pytest.mark.parametrize("raw", [None, float("nan")])
def test_fix_encoding_missing(raw):
    assert fix_encoding(raw) is None


def test_fix_encoding_never_raises_on_odd_input():
    assert fix_encoding("\x00\x01weird\uffff") is not None


# ------------------------------------------------------------------------------ url


def test_canonical_url_strips_context_param():
    """2.1 — the highest-stakes parser. All 51,717 URLs are unique only because of this."""
    a = "https://www.zomato.com/bangalore/jalsa-banashankari?context=eyJzZSI6eyJlIjpbNTg2OTQs"
    b = "https://www.zomato.com/bangalore/jalsa-banashankari?context=ZGlmZmVyZW50Y29udGV4dA=="
    assert canonical_url(a) == canonical_url(b)
    assert canonical_url(a) == "https://www.zomato.com/bangalore/jalsa-banashankari"


def test_canonical_url_strips_fragment_and_trailing_slash():
    assert (
        canonical_url("https://www.zomato.com/bangalore/jalsa/#reviews")
        == "https://www.zomato.com/bangalore/jalsa"
    )


@pytest.mark.parametrize("raw", [None, "", "   ", float("nan")])
def test_canonical_url_missing(raw):
    assert canonical_url(raw) is None


def test_dedup_key_prefers_url():
    key = dedup_key(
        "https://www.zomato.com/bangalore/jalsa?context=abc", name="Jalsa", address="942, 21st Main"
    )
    assert key == "https://www.zomato.com/bangalore/jalsa"


def test_dedup_key_falls_back_to_name_and_address():
    """2.2 — the (name, address) fallback must be stable across casing and spacing."""
    a = dedup_key(None, name="Jalsa", address="942, 21st Main Road")
    b = dedup_key(None, name="  JALSA  ", address="942,  21st   Main Road")
    assert a == b
    assert a


def test_dedup_key_distinguishes_outlets_at_different_addresses():
    """4.4 — 328 (name, location) pairs have multiple outlets; address must separate them."""
    a = dedup_key(None, name="Cafe Coffee Day", address="100 Feet Road, Indiranagar")
    b = dedup_key(None, name="Cafe Coffee Day", address="4th Block, Jayanagar")
    assert a != b


# -------------------------------------------------------------------------- reviews

_REVIEWS = (
    "[('Rated 4.0', 'RATED\\n  A beautiful place to dine in. The interiors are lovely.'), "
    "('Rated 2.0', 'RATED\\n  Food was ok ok. Definitely not visiting anymore.')]"
)


def test_extract_review_snippets_parses_and_strips_rated_prefix():
    out = extract_review_snippets(_REVIEWS)
    assert len(out) == 2
    assert out[0].startswith("A beautiful place")
    assert "RATED" not in out[0]
    assert "\n" not in out[0]  # 1.14 whitespace collapsed


def test_extract_review_snippets_respects_max_count():
    assert len(extract_review_snippets(_REVIEWS, max_snippets=1)) == 1


def test_extract_review_snippets_truncates():
    long_review = "[('Rated 4.0', 'RATED\\n  " + ("word " * 200) + "')]"
    out = extract_review_snippets(long_review, max_chars=50)
    assert len(out) == 1
    assert len(out[0]) <= 51  # 50 chars plus the ellipsis character


def test_extract_review_snippets_empty_list():
    """1.12 — 13% of rows carry an empty list; the UI must cope with no review text."""
    assert extract_review_snippets("[]") == []


@pytest.mark.parametrize(
    "raw",
    [
        None,
        float("nan"),
        "",
        "not a list at all",
        "[('Rated 4.0', unclosed",
        "[(1, 2, 3)]",
        "[None, None]",
        "__import__('os').system('echo pwned')",  # 1.13 must never be evaluated
    ],
)
def test_extract_review_snippets_never_raises(raw):
    """1.13 — one bad row must not abort a 51,717-row ingest."""
    assert extract_review_snippets(raw) == []


def test_extract_review_snippets_accepts_plain_string_entries():
    """A bare list of strings is a valid shape; extract it rather than discarding it."""
    assert extract_review_snippets("['Lovely place', 'Too crowded']") == [
        "Lovely place",
        "Too crowded",
    ]


def test_extract_review_snippets_repairs_encoding():
    raw = "[('Rated 5.0', 'RATED\\n  Great crÃ\x83Â¨me brulee here')]"
    assert "crème" in extract_review_snippets(raw)[0]


# ------------------------------------------------------------------- name normalize


def test_normalize_name_handles_short_names():
    """1.11 — 'HQ', 'F5', 'XU', 'B1' are real names; no minimum-length rejection."""
    for name in ("HQ", "F5", "XU", "B1"):
        assert normalize_name(name) == name.lower()


def test_normalize_name_is_stable_across_punctuation_and_case():
    assert normalize_name("Domino's Pizza") == normalize_name("Dominos  pizza")


def test_no_parser_raises_on_nan():
    """pandas hands NaN to every parser for missing object-column values."""
    nan = float("nan")
    assert math.isnan(nan)
    assert parse_rating(nan) is None
    assert parse_cost(nan) is None
    assert parse_yes_no(nan) is False
    assert split_multi(nan) == []
    assert fix_encoding(nan) is None
    assert canonical_url(nan) is None
    assert extract_review_snippets(nan) == []
