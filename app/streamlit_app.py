"""Streamlit entry point: preference form, results, and wiring only.

Every dropdown is populated from `facets.json`. Filtering and ranking stay in
`pipeline.recommend` — this file must not grow a second copy of that logic.
"""

from __future__ import annotations

import streamlit as st

from zomato_reco.config import settings
from zomato_reco.data.repository import ArtifactMissingError, ArtifactStaleError, get_repository
from zomato_reco.recommend.pipeline import recommend
from zomato_reco.ui.components import (
    budget_options,
    inject_css,
    neighborhood_options,
    occasion_options,
    preferences_from_form,
    render_initial,
    render_results,
)

st.set_page_config(
    page_title="Bangalore restaurant picks",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def _repository():
    return get_repository()


def _load_repo():
    try:
        return _repository()
    except ArtifactMissingError as exc:
        st.error(str(exc))
        st.stop()
    except ArtifactStaleError as exc:
        st.error(str(exc))
        st.stop()


inject_css()
repo = _load_repo()

st.markdown('<p class="reco-kicker">Bangalore · Zomato snapshot</p>', unsafe_allow_html=True)
st.title("Find a place to eat")
st.caption(
    "Neighborhoods, not cities — this catalog is Bangalore only, from a 2019 Zomato scrape. "
    f"{len(repo):,} restaurants. Many listings have since closed; treat links as historical."
)

with st.sidebar:
    st.header("Preferences")
    with st.form("preferences", clear_on_submit=False):
        location_choice = st.selectbox(
            "Neighborhood",
            neighborhood_options(repo),
            help="93 Bangalore neighborhoods. No city selector: this catalog is Bangalore-only.",
        )
        budget_choice = st.radio(
            "Budget",
            budget_options(repo),
            help="Calibrated on cost-for-two percentiles in this dataset, not even thirds.",
        )
        cuisines = st.multiselect(
            "Cuisines",
            repo.cuisines,
            help="Match any of the selected cuisines. Leave empty to include all.",
        )
        min_rating = st.slider(
            "Minimum rating",
            min_value=0.0,
            max_value=4.5,
            value=0.0,
            step=0.1,
            help="0 means any rating. Capped at 4.5 — almost nothing in the data sits higher.",
        )
        occasion_choice = st.selectbox("Occasion", occasion_options(repo))
        result_count = st.select_slider("How many results", options=[3, 5, 7, 10], value=5)
        extras = st.text_area(
            "Anything else?",
            placeholder="family-friendly, quick, good for a date…",
            max_chars=500,
            help="Keyword matching boosts the list; the LLM uses this for explanations.",
        )
        submitted = st.form_submit_button(
            "Find restaurants", type="primary", use_container_width=True
        )

if submitted:
    prefs = preferences_from_form(
        location_choice=location_choice,
        budget_choice=budget_choice,
        cuisines=cuisines,
        min_rating=min_rating,
        occasion_choice=occasion_choice,
        extras=extras,
        result_count=result_count,
    )
    with st.spinner("Finding restaurants…"):
        result = recommend(prefs, repo)
    st.session_state["result"] = result
    st.session_state["searched"] = True

if st.session_state.get("searched"):
    render_results(st.session_state["result"], llm_configured=settings.llm_enabled)
else:
    render_initial()
