"""Pure UI helpers: labels, formatting, and preference mapping. No Streamlit required."""

from zomato_reco.models import Restaurant, ScoreBreakdown, UserPreferences
from zomato_reco.ui.components import (
    ANY_BUDGET,
    ANY_NEIGHBORHOOD,
    ANY_OCCASION,
    _why_html,
    budget_choice_label,
    card_badges,
    cuisine_tags,
    display_name,
    format_cost,
    format_rating,
    min_rating_from_slider,
    preferences_from_form,
)


class FakeRepo:
    locations = ["Whitefield", "BTM"]
    meal_contexts = ["Dine-out"]
    cuisines = ["North Indian"]

    def budget_label(self, band: str) -> str:
        return {
            "low": "up to ₹300 for two",
            "medium": "₹301–₹500 for two",
            "high": "over ₹500 for two",
        }[band]


def test_budget_choice_uses_real_rupee_range():
    label = budget_choice_label(FakeRepo(), "medium")  # type: ignore[arg-type]
    assert "₹301–₹500 for two" in label
    assert label.startswith("medium")


def test_unrated_renders_as_not_yet_rated():
    assert format_rating(None) == "Not yet rated"
    assert "0.0" not in format_rating(None)
    assert format_rating(4.3, 1200) == "4.3/5 · 1,200 votes"


def test_cost_is_rupees_for_two():
    assert format_cost(600) == "₹600 for two"
    assert format_cost(None) == "Price not listed"


def test_cuisine_tags_cap_and_count_remainder():
    visible, extra = cuisine_tags(["a", "b", "c", "d", "e", "f"], limit=4)
    assert visible == ["a", "b", "c", "d"]
    assert extra == 2


def test_long_name_is_truncated():
    name = "A" * 90
    shown = display_name(name)
    assert shown.endswith("…")
    assert len(shown) <= 72


def test_empty_form_is_a_valid_query():
    prefs = preferences_from_form(
        location_choice=ANY_NEIGHBORHOOD,
        budget_choice=ANY_BUDGET,
        cuisines=[],
        min_rating=0.0,
        occasion_choice=ANY_OCCASION,
        extras="",
        result_count=5,
    )
    assert prefs == UserPreferences(result_count=5)


def test_slider_zero_means_no_minimum():
    assert min_rating_from_slider(0.0) is None
    assert min_rating_from_slider(4.5) == 4.5


def test_card_badges_only_true_flags():
    restaurant = Restaurant(id="1", name="Empire", online_order=True, is_quick_service=True)
    assert card_badges(restaurant) == ["Online ordering", "Quick service"]


def test_why_html_omitted_without_breakdown():
    assert _why_html(None, llm_used=True, open_=True) == ""


def test_why_html_open_top_card_and_llm_intro():
    html = _why_html(
        ScoreBreakdown(
            weighted_rating=4.32,
            preference_boost=0.4,
            score=4.72,
            votes=2841,
            strict_match=True,
        ),
        llm_used=True,
        open_=True,
    )
    assert 'class="reco-why" open>' in html
    assert "The AI chose the order" in html
    assert "4.32" in html
    assert "+0.40" in html
    assert "4.72" in html
    assert "2,841" in html
    assert "reco-yes" in html


def test_why_html_unrated_and_widened():
    html = _why_html(
        ScoreBreakdown(unrated=True, votes=0, strict_match=False),
        llm_used=False,
        open_=False,
    )
    assert 'class="reco-why">' in html
    assert "Not yet rated" in html
    assert "widened" in html
    assert "The AI chose" not in html
