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
    .stApp { background: #faf7f5; }
    section[data-testid="stSidebar"] { background: #fff; }
    h1 { letter-spacing: -0.03em; }
    .reco-kicker {
        color: #c2410c; font-weight: 650; font-size: 0.78rem;
        text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.2rem;
    }
    .reco-card {
        background: #fff; border: 1px solid #eadfd6; border-radius: 16px;
        padding: 1.1rem 1.25rem 0.85rem; margin-bottom: 0.9rem;
        box-shadow: 0 1px 0 rgba(28, 25, 23, 0.04);
    }
    .reco-rank {
        display: inline-flex; align-items: center; justify-content: center;
        width: 1.7rem; height: 1.7rem; border-radius: 999px;
        background: #9a3412; color: #fff; font-weight: 700; font-size: 0.85rem;
        margin-right: 0.55rem; flex-shrink: 0;
    }
    .reco-title { font-size: 1.15rem; font-weight: 700; color: #1c1917; word-break: break-word; }
    .reco-meta { color: #57534e; font-size: 0.92rem; margin: 0.35rem 0 0.55rem; }
    .reco-chip {
        display: inline-block; background: #fff7ed; color: #9a3412;
        border: 1px solid #fed7aa; border-radius: 999px;
        padding: 0.1rem 0.55rem; margin: 0 0.3rem 0.3rem 0; font-size: 0.78rem;
    }
    .reco-badge {
        display: inline-block; background: #f5f5f4; color: #44403c;
        border-radius: 6px; padding: 0.12rem 0.45rem; margin-right: 0.35rem;
        font-size: 0.75rem;
    }
    .reco-explain { color: #292524; line-height: 1.45; margin: 0.65rem 0 0.35rem; }
    .reco-link a { color: #9a3412 !important; font-weight: 600; }
    .reco-empty {
        background: #fff; border: 1px dashed #d6d3d1; border-radius: 16px;
        padding: 2rem 1.5rem; text-align: center; color: #57534e;
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
    import streamlit as st

    st.markdown(
        """
<div class="reco-empty">
  <p style="font-size:1.05rem;margin:0 0 0.4rem;color:#1c1917;font-weight:650;">
    Pick a Bangalore neighborhood to start
  </p>
  <p style="margin:0;">
    Filters on the left are filled from the dataset, so every choice is something
    the catalog can actually answer. Leave fields on “any” for a citywide shortlist.
  </p>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_zero_results(relaxations: Sequence[Relaxation]) -> None:
    import streamlit as st

    render_relaxations(relaxations)
    st.markdown(
        """
<div class="reco-empty">
  <p style="font-size:1.05rem;margin:0 0 0.4rem;color:#1c1917;font-weight:650;">
    Nothing matched, even after loosening the search
  </p>
  <p style="margin:0;">
    Try dropping a cuisine, lowering the rating floor, or picking a busier neighborhood.
    Thin areas such as Jakkur only have a handful of restaurants in this snapshot.
  </p>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_card(
    restaurant: Restaurant,
    recommendation: Recommendation,
    *,
    llm_used: bool,
) -> None:
    import streamlit as st

    tags, extra = cuisine_tags(restaurant.cuisines)
    chips = "".join(f'<span class="reco-chip">{_esc(tag)}</span>' for tag in tags)
    if extra:
        chips += f'<span class="reco-chip">+{extra} more</span>'

    badges = []
    if restaurant.online_order:
        badges.append("Online ordering")
    if restaurant.book_table:
        badges.append("Table booking")
    if restaurant.is_family_friendly:
        badges.append("Family friendly")
    if restaurant.is_quick_service:
        badges.append("Quick service")
    badge_html = "".join(f'<span class="reco-badge">{_esc(b)}</span>' for b in badges)

    location = restaurant.location or "Bangalore"
    address = restaurant.address or ""
    meta_bits = [
        format_rating(restaurant.rating, restaurant.votes),
        format_cost(restaurant.cost_for_two),
        location,
    ]
    meta = " · ".join(meta_bits)

    link = ""
    if restaurant.url:
        link = (
            f'<div class="reco-link" style="margin-top:0.4rem;">'
            f'<a href="{_esc(restaurant.url)}" target="_blank" rel="noopener">View on Zomato</a>'
            f"</div>"
        )

    st.markdown(
        f"""
<div class="reco-card">
  <div style="display:flex;align-items:flex-start;">
    <span class="reco-rank">{recommendation.rank}</span>
    <div style="min-width:0;">
      <div class="reco-title">{_esc(display_name(restaurant.name))}</div>
      <div class="reco-meta">{_esc(meta)}</div>
      <div>{chips}</div>
      <div style="margin-top:0.25rem;">{badge_html}</div>
      <p class="reco-explain">{_esc(recommendation.explanation)}</p>
      {f'<div class="reco-meta">{_esc(address)}</div>' if address else ""}
      {link}
    </div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
    _render_why(recommendation.score_breakdown, llm_used=llm_used)


def _render_why(breakdown: ScoreBreakdown | None, *, llm_used: bool) -> None:
    import streamlit as st

    if breakdown is None:
        return
    with st.expander("Why this rank"):
        if llm_used:
            st.caption(
                "The AI chose the order. These numbers are the deterministic score underneath."
            )
        if breakdown.unrated:
            st.write("Not yet rated — sorted after every rated match.")
        else:
            st.write(
                f"Vote-weighted rating: **{breakdown.weighted_rating:.2f}**"
                if breakdown.weighted_rating is not None
                else "Vote-weighted rating: —"
            )
            st.write(f"Preference boost: **{breakdown.preference_boost:+.2f}**")
            if breakdown.score is not None:
                st.write(f"Combined score: **{breakdown.score:.2f}**")
        st.write(f"Votes (tie-break): **{breakdown.votes:,}**")
        if breakdown.strict_match:
            st.write("Matched the original filters: **yes**")
        else:
            st.write("Admitted after the search was widened.")


def render_results(result: RecommendationResult, *, llm_configured: bool) -> None:
    if not result.restaurants:
        render_zero_results(result.relaxations)
        return
    if not result.llm_used:
        render_degraded_banner(llm_configured=llm_configured)
    render_relaxations(result.relaxations)
    if result.summary:
        import streamlit as st

        st.markdown(result.summary)
    for restaurant, rec in zip(result.restaurants, result.recommendations, strict=False):
        render_card(restaurant, rec, llm_used=result.llm_used)


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
