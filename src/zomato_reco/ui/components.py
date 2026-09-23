"""Streamlit rendering helpers. No filtering or ranking lives here — only display."""

from __future__ import annotations

from collections.abc import Sequence

from zomato_reco.data.repository import Repository
from zomato_reco.models import (
    Recommendation,
    RecommendationResult,
    Relaxation,
    Restaurant,
    ScoreBreakdown,
    UserPreferences,
)

ANY_NEIGHBORHOOD = "Any neighborhood"
ANY_BUDGET = "Any budget"
ANY_OCCASION = "Any occasion"

BUDGET_ORDER = ("low", "medium", "high")
MAX_CUISINE_TAGS = 4
MAX_NAME_CHARS = 72


def neighborhood_options(repo: Repository) -> list[str]:
    return [ANY_NEIGHBORHOOD, *repo.locations]


def budget_options(repo: Repository) -> list[str]:
    return [ANY_BUDGET, *[budget_choice_label(repo, band) for band in BUDGET_ORDER]]


def occasion_options(repo: Repository) -> list[str]:
    return [ANY_OCCASION, *repo.meal_contexts]


def budget_choice_label(repo: Repository, band: str) -> str:
    """Radio labels with the real rupee ranges, not implied even thirds."""
    return f"{band} — {repo.budget_label(band)}"


def band_from_choice(label: str) -> str | None:
    if not label or label == ANY_BUDGET:
        return None
    return label.split(" — ", 1)[0]


def location_from_choice(label: str) -> str | None:
    if not label or label == ANY_NEIGHBORHOOD:
        return None
    return label


def occasion_from_choice(label: str) -> str | None:
    if not label or label == ANY_OCCASION:
        return None
    return label


def min_rating_from_slider(value: float) -> float | None:
    """0 on the slider means 'any rating'; the data floor is 1.8 and 4.5 is the cap."""
    if value is None or value <= 0:
        return None
    return round(float(value), 1)


def preferences_from_form(
    *,
    location_choice: str,
    budget_choice: str,
    cuisines: Sequence[str],
    min_rating: float,
    occasion_choice: str,
    extras: str,
    result_count: int,
) -> UserPreferences:
    return UserPreferences(
        location=location_from_choice(location_choice),
        budget=band_from_choice(budget_choice),  # type: ignore[arg-type]
        cuisines=list(cuisines),
        min_rating=min_rating_from_slider(min_rating),
        meal_context=occasion_from_choice(occasion_choice),
        extras=extras or "",
        result_count=int(result_count),
    )


def format_rating(rating: float | None, votes: int = 0) -> str:
    if rating is None:
        return "Not yet rated"
    votes_bit = f" · {votes:,} votes" if votes else ""
    return f"{rating:.1f}/5{votes_bit}"


def format_cost(cost: int | None) -> str:
    if cost is None:
        return "Price not listed"
    return f"₹{int(cost):,} for two"


def display_name(name: str) -> str:
    text = name.strip()
    if len(text) <= MAX_NAME_CHARS:
        return text
    return text[: MAX_NAME_CHARS - 1].rstrip() + "…"


def cuisine_tags(cuisines: Sequence[str], limit: int = MAX_CUISINE_TAGS) -> tuple[list[str], int]:
    visible = [c for c in cuisines if c][:limit]
    extra = max(0, len(cuisines) - len(visible))
    return visible, extra


def inject_css() -> None:
    import streamlit as st

    st.markdown(
        """
<style>
    .stAppDeployButton, footer { display: none; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] {
        max-width: 856px; margin: 0; padding: 2.5rem 3rem 3rem;
    }
    h1 {
        font-size: 2.5rem !important; font-weight: 650 !important;
        letter-spacing: -0.03em; line-height: 1.1 !important; padding: 0 0 0.6rem !important;
    }
    [data-testid="stCaptionContainer"] p { color: #A8A29E; font-size: 0.875rem; line-height: 1.6; }

    section[data-testid="stSidebar"] {
        border-right: 1px solid #3A342E;
    }
    @media (min-width: 769px) {
        section[data-testid="stSidebar"] { min-width: 320px; }
    }
    section[data-testid="stSidebar"] > div:first-child { padding: 1.5rem 1.25rem 1.25rem; }
    section[data-testid="stSidebar"] [data-testid="stForm"] {
        border: none; padding: 0; background: transparent;
    }
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
        text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.72rem;
        font-weight: 600; color: #A8A29E;
    }
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
        font-size: 0.7rem; color: rgba(168, 162, 158, 0.8); line-height: 1.4;
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] > div,
    section[data-testid="stSidebar"] [data-baseweb="input"] > div,
    section[data-testid="stSidebar"] textarea {
        background-color: #12100E !important; border-color: #3A342E !important;
        color: #FAF7F5 !important;
    }
    section[data-testid="stSidebar"] [data-baseweb="tag"] {
        background-color: #3A2418 !important; color: #FDBA74 !important;
        border: 1px solid #9A3412 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
        background: #231F1C; border: 1px solid rgba(58, 52, 46, 0.6);
        border-radius: 6px; padding: 0.35rem 0.45rem; margin-left: -0.45rem;
        color: #FAF7F5;
    }
    section[data-testid="stSidebar"] [data-testid="stSliderThumbValue"] {
        color: #FBBF24 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSliderTickBarMin"],
    section[data-testid="stSidebar"] [data-testid="stSliderTickBarMax"] {
        color: rgba(168, 162, 158, 0.6) !important;
    }
    section[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[kind="secondary"],
    section[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[aria-checked="false"] {
        background: transparent !important; color: #A8A29E !important; border: none !important;
    }
    section[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[kind="primary"],
    section[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[aria-checked="true"] {
        background: #EA580C !important; color: #fff !important; border: none !important;
    }
    section[data-testid="stSidebar"] [data-testid="stFormSubmitButton"] button {
        background: #C2410C; border-color: #C2410C; color: #FAF7F5; font-weight: 600;
    }
    section[data-testid="stSidebar"] [data-testid="stFormSubmitButton"] button:hover {
        background: #EA580C; border-color: #EA580C; color: #FAF7F5;
    }

    [data-testid="stAlert"] {
        background: #231F1C; border: 1px solid #3A342E; color: #FAF7F5;
    }

    .reco-sidebar-head {
        display: flex; align-items: center; justify-content: space-between; gap: 0.75rem;
        padding-bottom: 1.1rem; margin-bottom: 0.35rem;
        border-bottom: 1px solid rgba(58, 52, 46, 0.7);
    }
    .reco-sidebar-title {
        font-size: 1.125rem; font-weight: 600; letter-spacing: -0.01em; color: #FAF7F5;
    }
    .reco-filters-badge {
        font-size: 0.7rem; font-weight: 500; color: #A8A29E; background: #2C2825;
        border: 1px solid rgba(58, 52, 46, 0.5); border-radius: 4px; padding: 0.15rem 0.5rem;
    }
    .reco-sidebar-foot {
        display: flex; justify-content: space-between; margin-top: 1.5rem; padding-top: 1.25rem;
        border-top: 1px solid rgba(58, 52, 46, 0.5); font-size: 0.7rem;
        color: rgba(168, 162, 158, 0.7);
    }

    .reco-kicker {
        color: #EA580C; font-weight: 700; font-size: 0.75rem;
        text-transform: uppercase; letter-spacing: 0.12em; margin-bottom: 0.1rem;
    }
    .reco-summary {
        color: #FAF7F5; font-size: 0.875rem; line-height: 1.6;
        padding-bottom: 1rem; margin-bottom: 1.5rem; border-bottom: 1px solid rgba(58, 52, 46, 0.6);
    }
    .reco-card {
        display: flex; align-items: flex-start; gap: 0.875rem;
        background: #231F1C; border: 1px solid #3A342E; border-radius: 16px;
        padding: 1rem; margin-bottom: 0.75rem;
    }
    .reco-rank {
        display: inline-flex; align-items: center; justify-content: center;
        width: 1.75rem; height: 1.75rem; border-radius: 999px; margin-top: 0.125rem;
        background: #EA580C; color: #fff; font-weight: 700; font-size: 0.875rem; flex-shrink: 0;
    }
    .reco-body { flex: 1; min-width: 0; }
    .reco-head {
        display: flex; align-items: baseline; justify-content: space-between; gap: 0.5rem;
    }
    .reco-title {
        font-size: 1.125rem; font-weight: 700; color: #FAF7F5; line-height: 1.35;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .reco-head a {
        color: #EA580C !important; font-size: 0.75rem; font-weight: 600;
        text-decoration: none; flex-shrink: 0;
    }
    .reco-head a:hover { text-decoration: underline; }
    .reco-meta { color: #A8A29E; font-size: 0.875rem; margin-top: 0.125rem; }
    .reco-meta .reco-sep { margin: 0 0.35rem; }
    .reco-rating { color: #FBBF24; font-weight: 600; }
    .reco-tags {
        display: flex; flex-wrap: wrap; align-items: center; gap: 0.375rem; margin-top: 0.625rem;
    }
    .reco-chip {
        background: #3A2418; color: #FDBA74; border: 1px solid #9A3412; border-radius: 4px;
        padding: 0.05rem 0.5rem; font-size: 0.75rem; font-weight: 500;
    }
    .reco-divider { width: 1px; height: 0.75rem; background: #3A342E; margin: 0 0.25rem; }
    .reco-badge {
        background: #2C2825; color: #D6D3D1; border-radius: 4px;
        padding: 0.05rem 0.5rem; font-size: 0.75rem;
    }
    .reco-explain { color: #FAF7F5; font-size: 0.875rem; line-height: 1.6; margin: 0.75rem 0 0; }
    .reco-address { color: #A8A29E; font-size: 0.75rem; margin: 0.5rem 0 0; }

    .reco-why {
        margin-top: 0.75rem; padding-top: 0.625rem; border-top: 1px solid rgba(58, 52, 46, 0.5);
    }
    .reco-why summary {
        display: flex; align-items: center; gap: 0.375rem; list-style: none; cursor: pointer;
        color: #A8A29E; font-size: 0.75rem; font-weight: 500;
    }
    .reco-why summary::-webkit-details-marker { display: none; }
    .reco-why summary::before {
        content: ""; width: 0.4rem; height: 0.4rem; margin: 0 0.15rem 0.15rem 0.1rem;
        border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor;
        transform: rotate(45deg); transition: transform 0.15s;
    }
    .reco-why summary:hover { color: #FAF7F5; }
    .reco-why[open] summary { color: #FAF7F5; }
    .reco-why[open] summary::before {
        color: #EA580C; transform: rotate(-135deg); margin-bottom: -0.15rem;
    }
    .reco-why-panel {
        margin-top: 0.5rem; padding: 0.75rem; border-radius: 8px;
        background: #1A1714; border: 1px solid rgba(58, 52, 46, 0.8); font-size: 0.75rem;
    }
    .reco-why-panel p { color: #FAF7F5; margin: 0 0 0.5rem; }
    .reco-why-stats { display: flex; flex-wrap: wrap; gap: 0.25rem 1rem; color: #A8A29E; }
    .reco-why-stats b { color: #FAF7F5; font-weight: 500; }
    .reco-why-stats .reco-boost { color: #EA580C; }
    .reco-why-stats .reco-score { color: #FBBF24; font-weight: 700; }
    .reco-why-stats .reco-yes { color: #34D399; }

    .reco-empty {
        background: #1A1714; border: 1px dashed #3A342E; border-radius: 16px;
        padding: 2rem 1.5rem; text-align: center; color: #A8A29E;
    }
    .reco-empty-title { font-size: 1.05rem; margin: 0 0 0.4rem; color: #FAF7F5; font-weight: 650; }
    .reco-foot {
        display: flex; justify-content: space-between; margin-top: 2.5rem; padding-top: 1.5rem;
        border-top: 1px solid rgba(58, 52, 46, 0.5); font-size: 0.75rem;
        color: rgba(168, 162, 158, 0.6);
    }

    @media (max-width: 768px) {
        [data-testid="stMainBlockContainer"] {
            max-width: 100%; padding: 1.25rem 1rem 2.5rem;
        }
        section[data-testid="stSidebar"] { min-width: 0; }
        h1 { font-size: 1.85rem !important; }
        .reco-head { flex-wrap: wrap; }
        .reco-title { white-space: normal; }
        .reco-foot { flex-direction: column; gap: 0.35rem; }
    }
</style>
        """,
        unsafe_allow_html=True,
    )


def render_relaxations(relaxations: Sequence[Relaxation]) -> None:
    import streamlit as st

    if not relaxations:
        return
    st.markdown("##### Search was widened")
    for item in relaxations:
        st.warning(item.detail, icon="↔️")


def render_degraded_banner(*, llm_configured: bool) -> None:
    import streamlit as st

    if llm_configured:
        st.info(
            "AI ranking was skipped (quota, timeout, or a provider error). "
            "You're seeing the vote-weighted order with template explanations.",
            icon="ℹ️",
        )
    else:
        st.info(
            "No LLM API key configured — rankings use vote-weighted scores and "
            "template explanations. Add `GROQ_API_KEY` to `.env` for AI-written reasons.",
            icon="ℹ️",
        )


def render_initial() -> None:
    _render_empty(
        "Pick a Bangalore neighborhood to start",
        "Filters on the left are filled from the dataset, so every choice is something "
        "the catalog can actually answer. Leave fields on “any” for a citywide shortlist.",
    )


def render_zero_results(relaxations: Sequence[Relaxation]) -> None:
    render_relaxations(relaxations)
    _render_empty(
        "Nothing matched, even after loosening the search",
        "Try dropping a cuisine, lowering the rating floor, or picking a busier neighborhood. "
        "Thin areas such as Jakkur only have a handful of restaurants in this snapshot.",
    )


def _render_empty(title: str, body: str) -> None:
    import streamlit as st

    st.markdown(
        f'<div class="reco-empty"><p class="reco-empty-title">{_esc(title)}</p>'
        f'<p style="margin:0;">{_esc(body)}</p></div>',
        unsafe_allow_html=True,
    )


def render_sidebar_header(*, filters_active: bool) -> None:
    import streamlit as st

    badge = (
        '<span class="reco-filters-badge">Filters Active</span>' if filters_active else ""
    )
    st.markdown(
        f'<div class="reco-sidebar-head"><span class="reco-sidebar-title">Preferences</span>'
        f"{badge}</div>",
        unsafe_allow_html=True,
    )


def render_sidebar_footer(restaurant_count: int) -> None:
    import streamlit as st

    st.markdown(
        f'<div class="reco-sidebar-foot"><span>Catalog: Bangalore 2019</span>'
        f"<span>{restaurant_count:,} entries</span></div>",
        unsafe_allow_html=True,
    )


def card_badges(restaurant: Restaurant) -> list[str]:
    flags = (
        (restaurant.online_order, "Online ordering"),
        (restaurant.book_table, "Table booking"),
        (restaurant.is_family_friendly, "Family friendly"),
        (restaurant.is_quick_service, "Quick service"),
    )
    return [label for on, label in flags if on]


def render_card(
    restaurant: Restaurant,
    recommendation: Recommendation,
    *,
    llm_used: bool,
) -> None:
    import streamlit as st

    tags, extra = cuisine_tags(restaurant.cuisines)
    chips = [f'<span class="reco-chip">{_esc(tag)}</span>' for tag in tags]
    if extra:
        chips.append(f'<span class="reco-chip">+{extra} more</span>')
    badges = [f'<span class="reco-badge">{_esc(b)}</span>' for b in card_badges(restaurant)]
    divider = '<span class="reco-divider"></span>' if chips and badges else ""
    tags_html = (
        f'<div class="reco-tags">{"".join(chips)}{divider}{"".join(badges)}</div>'
        if chips or badges
        else ""
    )

    rating_class = "reco-rating" if restaurant.rating is not None else ""
    meta_bits = [f'<span class="{rating_class}">{_esc(format_rating(restaurant.rating))}</span>']
    if restaurant.votes:
        meta_bits.append(f"{restaurant.votes:,} votes")
    meta_bits.append(_esc(format_cost(restaurant.cost_for_two)))
    meta_bits.append(_esc(restaurant.location or "Bangalore"))
    meta = '<span class="reco-sep">·</span>'.join(meta_bits)

    link = (
        f'<a href="{_esc(restaurant.url)}" target="_blank" rel="noopener">View on Zomato</a>'
        if restaurant.url
        else ""
    )
    address = (
        f'<p class="reco-address">{_esc(restaurant.address)}</p>' if restaurant.address else ""
    )
    why = _why_html(
        recommendation.score_breakdown, llm_used=llm_used, open_=recommendation.rank == 1
    )

    st.markdown(
        "".join(
            [
                '<div class="reco-card">',
                f'<span class="reco-rank">{recommendation.rank}</span>',
                '<div class="reco-body">',
                '<div class="reco-head">',
                f'<div class="reco-title">{_esc(display_name(restaurant.name))}</div>',
                link,
                "</div>",
                f'<div class="reco-meta">{meta}</div>',
                tags_html,
                f'<p class="reco-explain">{_esc(recommendation.explanation)}</p>',
                address,
                why,
                "</div></div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _why_html(breakdown: ScoreBreakdown | None, *, llm_used: bool, open_: bool) -> str:
    if breakdown is None:
        return ""
    intro = (
        "<p>The AI chose the order. These numbers are the deterministic score underneath.</p>"
        if llm_used
        else ""
    )
    stats: list[str] = []
    if breakdown.unrated:
        stats.append("<span>Not yet rated — sorted after every rated match.</span>")
    else:
        weighted = (
            f"{breakdown.weighted_rating:.2f}" if breakdown.weighted_rating is not None else "—"
        )
        stats.append(f"<span>Vote-weighted rating: <b>{weighted}</b></span>")
        stats.append(
            f'<span>Preference boost: <b class="reco-boost">{breakdown.preference_boost:+.2f}</b>'
            "</span>"
        )
        if breakdown.score is not None:
            stats.append(
                f'<span>Combined score: <b class="reco-score">{breakdown.score:.2f}</b></span>'
            )
    stats.append(f"<span>Votes (tie-break): <b>{breakdown.votes:,}</b></span>")
    if breakdown.strict_match:
        stats.append('<span>Matched the original filters: <b class="reco-yes">yes</b></span>')
    else:
        stats.append("<span>Admitted after the search was widened.</span>")
    return (
        f'<details class="reco-why"{" open" if open_ else ""}>'
        "<summary>Why this rank</summary>"
        f'<div class="reco-why-panel">{intro}<div class="reco-why-stats">{"".join(stats)}</div>'
        "</div></details>"
    )


def render_results(result: RecommendationResult, *, llm_configured: bool) -> None:
    import streamlit as st

    if not result.restaurants:
        render_zero_results(result.relaxations)
        return
    if not result.llm_used:
        render_degraded_banner(llm_configured=llm_configured)
    render_relaxations(result.relaxations)
    if result.summary:
        st.markdown(
            f'<div class="reco-summary">{_esc(result.summary)}</div>', unsafe_allow_html=True
        )
    for restaurant, rec in zip(result.restaurants, result.recommendations, strict=False):
        render_card(restaurant, rec, llm_used=result.llm_used)
    count = len(result.restaurants)
    st.markdown(
        f'<div class="reco-foot"><span>End of {count} shortlisted '
        f'recommendation{"" if count == 1 else "s"}</span>'
        "<span>Historical snapshot (2019)</span></div>",
        unsafe_allow_html=True,
    )


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
