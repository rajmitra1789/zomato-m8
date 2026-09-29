const API_BASE = (window.API_BASE || "http://localhost:8000").replace(/\/$/, "");
const ANY_NEIGHBORHOOD = "Any neighborhood";
const ANY_OCCASION = "Any occasion";
const RESULT_COUNTS = [3, 5, 7, 10];
const MAX_CUISINE_TAGS = 4;
const MAX_NAME_CHARS = 72;

const state = {
  facets: null,
  llmConfigured: false,
  cuisines: [],
  resultCount: 5,
  searched: false,
};

const $ = (id) => document.getElementById(id);

function formatCount(value) {
  return Number(value).toLocaleString("en-IN");
}

function displayName(name) {
  const text = String(name || "").trim();
  if (text.length <= MAX_NAME_CHARS) return text;
  return `${text.slice(0, MAX_NAME_CHARS - 1).trimEnd()}…`;
}

function formatCost(cost) {
  if (cost == null) return "Price not listed";
  return `₹${formatCount(cost)} for two`;
}

function ratingReadout(value) {
  if (value <= 0) return "Any";
  return `${value.toFixed(1)} and above`;
}

function show(node, visible) {
  node.hidden = !visible;
}

function emptyPanel(title, body) {
  const wrap = document.createElement("div");
  wrap.className = "empty";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = body;
  wrap.append(heading, copy);
  return wrap;
}

function renderInitial() {
  $("results").replaceChildren(
    emptyPanel(
      "Pick a Bangalore neighborhood to start",
      "Filters on the left are filled from the dataset, so every choice is something the catalog can actually answer. Leave fields on “any” for a citywide shortlist.",
    ),
  );
  show($("banner"), false);
  show($("relaxations"), false);
  show($("summary"), false);
  show($("foot"), false);
}

function fillSelect(select, values, anyLabel) {
  select.replaceChildren();
  const any = document.createElement("option");
  any.value = "";
  any.textContent = anyLabel;
  select.append(any);
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
}

function budgetLabel(band, bands) {
  const title = band.charAt(0).toUpperCase() + band.slice(1);
  return `${title} — ${bands[band].label}`;
}

function renderBudget(bands) {
  const host = $("budget-options");
  host.replaceChildren();
  const choices = [
    { value: "", label: "Any budget" },
    { value: "low", label: budgetLabel("low", bands) },
    { value: "medium", label: budgetLabel("medium", bands) },
    { value: "high", label: budgetLabel("high", bands) },
  ];
  for (const choice of choices) {
    const label = document.createElement("label");
    label.className = "budget-option";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "budget";
    input.value = choice.value;
    if (!choice.value) input.checked = true;
    const text = document.createElement("span");
    text.textContent = choice.label;
    label.append(input, text);
    host.append(label);
  }
}

function renderResultCounts() {
  const host = $("result-count");
  host.replaceChildren();
  for (const count of RESULT_COUNTS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = String(count);
    button.setAttribute("aria-pressed", String(count === state.resultCount));
    button.addEventListener("click", () => {
      state.resultCount = count;
      for (const peer of host.querySelectorAll("button")) {
        peer.setAttribute("aria-pressed", String(peer === button));
      }
    });
    host.append(button);
  }
}

function renderCuisineChips() {
  const host = $("cuisine-chips");
  host.replaceChildren();
  for (const name of state.cuisines) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.append(document.createTextNode(name));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${name}`);
    remove.addEventListener("click", () => {
      state.cuisines = state.cuisines.filter((item) => item !== name);
      renderCuisineChips();
    });
    chip.append(remove);
    host.append(chip);
  }
}

function addCuisine(name) {
  const known = state.facets?.cuisines || [];
  const match = known.find((item) => item.toLowerCase() === name.trim().toLowerCase());
  if (!match || state.cuisines.includes(match)) return;
  state.cuisines.push(match);
  renderCuisineChips();
  $("cuisine-input").value = "";
  hideSuggestions();
}

function hideSuggestions() {
  const list = $("cuisine-suggest");
  list.replaceChildren();
  list.hidden = true;
}

function showSuggestions() {
  const query = $("cuisine-input").value.trim().toLowerCase();
  const list = $("cuisine-suggest");
  if (!query || !state.facets) {
    hideSuggestions();
    return;
  }
  const matches = state.facets.cuisines
    .filter((name) => name.toLowerCase().includes(query) && !state.cuisines.includes(name))
    .slice(0, 8);
  list.replaceChildren();
  if (!matches.length) {
    list.hidden = true;
    return;
  }
  for (const name of matches) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = name;
    button.addEventListener("mousedown", (event) => {
      event.preventDefault();
      addCuisine(name);
    });
    item.append(button);
    list.append(item);
  }
  list.hidden = false;
}

function preferences() {
  const rating = Number($("min-rating").value);
  const budget = document.querySelector('input[name="budget"]:checked')?.value || "";
  const occasion = $("occasion").value;
  return {
    location: $("location").value || null,
    budget: budget || null,
    cuisines: state.cuisines,
    min_rating: rating > 0 ? Math.round(rating * 10) / 10 : null,
    meal_context: occasion || null,
    extras: $("extras").value.trim(),
    result_count: state.resultCount,
  };
}

function capabilityBadges(restaurant) {
  return [
    restaurant.online_order ? "Online ordering" : null,
    restaurant.book_table ? "Table booking" : null,
    restaurant.is_family_friendly ? "Family friendly" : null,
    restaurant.is_quick_service ? "Quick service" : null,
  ].filter(Boolean);
}

function metaLine(restaurant) {
  const line = document.createElement("div");
  line.className = "meta";
  const bits = [];
  const rating = document.createElement("span");
  if (restaurant.rating == null) {
    rating.textContent = "Not yet rated";
  } else {
    rating.className = "rating";
    rating.textContent = `${Number(restaurant.rating).toFixed(1)}/5`;
  }
  bits.push(rating);
  if (restaurant.votes) bits.push(document.createTextNode(`${formatCount(restaurant.votes)} votes`));
  bits.push(document.createTextNode(formatCost(restaurant.cost_for_two)));
  bits.push(document.createTextNode(restaurant.location || "Bangalore"));
  bits.forEach((bit, index) => {
    if (index) line.append(document.createTextNode("·"));
    line.append(bit);
  });
  return line;
}

function tagRow(restaurant) {
  const cuisines = (restaurant.cuisines || []).filter(Boolean);
  const visible = cuisines.slice(0, MAX_CUISINE_TAGS);
  const extra = cuisines.length - visible.length;
  const badges = capabilityBadges(restaurant);
  if (!visible.length && !badges.length) return null;
  const row = document.createElement("div");
  row.className = "tags";
  for (const name of visible) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name;
    row.append(chip);
  }
  if (extra > 0) {
    const more = document.createElement("span");
    more.className = "chip";
    more.textContent = `+${extra} more`;
    row.append(more);
  }
  if (visible.length && badges.length) {
    const rule = document.createElement("span");
    rule.className = "tag-rule";
    row.append(rule);
  }
  for (const label of badges) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = label;
    row.append(badge);
  }
  return row;
}

function whyBlock(breakdown, llmUsed, open) {
  if (!breakdown) return null;
  const details = document.createElement("details");
  details.className = "why";
  if (open) details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Why this rank";
  const panel = document.createElement("div");
  panel.className = "why-panel";
  if (llmUsed) {
    const intro = document.createElement("p");
    intro.textContent = "The AI chose the order. These numbers are the deterministic score underneath.";
    panel.append(intro);
  }
  const stats = document.createElement("div");
  stats.className = "why-stats";
  const add = (label, value, className) => {
    const span = document.createElement("span");
    span.append(document.createTextNode(`${label} `));
    const strong = document.createElement("b");
    if (className) strong.className = className;
    strong.textContent = value;
    span.append(strong);
    stats.append(span);
  };
  if (breakdown.unrated) {
    const span = document.createElement("span");
    span.textContent = "Not yet rated — sorted after every rated match.";
    stats.append(span);
  } else {
    const weighted = breakdown.weighted_rating == null ? "—" : Number(breakdown.weighted_rating).toFixed(2);
    add("Vote-weighted rating:", weighted);
    const boost = Number(breakdown.preference_boost || 0);
    add("Preference boost:", `${boost >= 0 ? "+" : ""}${boost.toFixed(2)}`, "boost");
    if (breakdown.score != null) add("Combined score:", Number(breakdown.score).toFixed(2), "score");
  }
  add("Votes:", formatCount(breakdown.votes || 0));
  if (breakdown.strict_match) add("Matched the original filters:", "yes", "yes");
  else {
    const span = document.createElement("span");
    span.textContent = "Admitted after the search was widened.";
    stats.append(span);
  }
  panel.append(stats);
  details.append(summary, panel);
  return details;
}

function renderCard(restaurant, recommendation, llmUsed) {
  const card = document.createElement("article");
  card.className = "card";
  const row = document.createElement("div");
  row.className = "card-row";
  const rank = document.createElement("div");
  rank.className = "rank";
  rank.textContent = String(recommendation.rank);
  const body = document.createElement("div");
  body.className = "card-body";
  const head = document.createElement("div");
  head.className = "card-head";
  const title = document.createElement("h3");
  title.className = "card-title";
  title.textContent = displayName(restaurant.name);
  head.append(title);
  if (restaurant.url) {
    const link = document.createElement("a");
    link.className = "zomato";
    link.href = restaurant.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "View on Zomato";
    head.append(link);
  }
  body.append(head, metaLine(restaurant));
  const tags = tagRow(restaurant);
  if (tags) body.append(tags);
  const explain = document.createElement("p");
  explain.className = "explain";
  explain.textContent = recommendation.explanation || "";
  body.append(explain);
  if (restaurant.address) {
    const address = document.createElement("p");
    address.className = "address";
    address.textContent = restaurant.address;
    body.append(address);
  }
  const why = whyBlock(recommendation.score_breakdown, llmUsed, recommendation.rank === 1);
  if (why) body.append(why);
  row.append(rank, body);
  card.append(row);
  return card;
}

function renderRelaxations(items) {
  const host = $("relaxations");
  host.replaceChildren();
  if (!items?.length) {
    show(host, false);
    return;
  }
  for (const item of items) {
    const note = document.createElement("p");
    note.className = "notice";
    note.textContent = item.detail;
    host.append(note);
  }
  show(host, true);
}

function renderDegraded(llmConfigured) {
  const banner = $("banner");
  banner.textContent = llmConfigured
    ? "AI ranking was skipped (quota, timeout, or a provider error). You're seeing the vote-weighted order with template explanations."
    : "No LLM API key is configured on the server — rankings use vote-weighted scores and template explanations.";
  show(banner, true);
}

function renderResult(result) {
  state.searched = true;
  show($("filters-badge"), true);
  renderRelaxations(result.relaxations);
  const summary = $("summary");
  if (result.summary) {
    summary.textContent = result.summary;
    show(summary, true);
  } else {
    show(summary, false);
  }

  const host = $("results");
  if (!result.restaurants?.length) {
    show($("banner"), false);
    show($("foot"), false);
    host.replaceChildren(
      emptyPanel(
        "Nothing matched, even after loosening the search",
        "Try dropping a cuisine, lowering the rating floor, or picking a busier neighborhood. Thin areas such as Jakkur only have a handful of restaurants in this snapshot.",
      ),
    );
    return;
  }

  if (!result.llm_used) renderDegraded(state.llmConfigured);
  else show($("banner"), false);

  const cards = document.createElement("div");
  cards.className = "cards";
  const count = Math.min(result.restaurants.length, result.recommendations.length);
  for (let index = 0; index < count; index += 1) {
    cards.append(renderCard(result.restaurants[index], result.recommendations[index], result.llm_used));
  }
  host.replaceChildren(cards);
  $("foot-count").textContent = `End of ${count} shortlisted recommendation${count === 1 ? "" : "s"}`;
  show($("foot"), true);
}

function renderError(message) {
  $("results").replaceChildren(emptyPanel("Could not reach the recommendation API", message));
  show($("banner"), false);
  show($("relaxations"), false);
  show($("summary"), false);
  show($("foot"), false);
}

async function loadFacets() {
  const [facetsResponse, healthResponse] = await Promise.all([
    fetch(`${API_BASE}/api/facets`),
    fetch(`${API_BASE}/health`),
  ]);
  if (!facetsResponse.ok) {
    throw new Error(`Facets request failed (${facetsResponse.status}). Is the API running at ${API_BASE}?`);
  }
  const facets = await facetsResponse.json();
  state.facets = facets;
  if (healthResponse.ok) {
    state.llmConfigured = Boolean((await healthResponse.json()).llm);
  }
  fillSelect($("location"), facets.locations, ANY_NEIGHBORHOOD);
  fillSelect($("occasion"), facets.meal_contexts, ANY_OCCASION);
  renderBudget(facets.budget_bands);
  $("location-hint").textContent = `${facets.locations.length} Bangalore neighborhoods.`;
  $("catalog-count").textContent = `${formatCount(facets.restaurant_count)} entries`;
  const count = formatCount(facets.restaurant_count);
  $("lede").textContent =
    `Neighborhoods, not cities — this catalog is Bangalore only, from a 2019 Zomato scrape. ${count} restaurants. Many listings have since closed; treat links as historical.`;
}

async function onSubmit(event) {
  event.preventDefault();
  const pending = $("cuisine-input").value.trim();
  if (pending) addCuisine(pending);
  const button = $("submit");
  button.disabled = true;
  button.textContent = "Finding restaurants…";
  $("results").replaceChildren(emptyPanel("Finding restaurants…", "Ranking the catalog against your filters."));
  try {
    const response = await fetch(`${API_BASE}/api/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preferences()),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Search failed (${response.status}).`);
    }
    renderResult(await response.json());
  } catch (error) {
    renderError(error.message || "Search failed.");
  } finally {
    button.disabled = false;
    button.textContent = "Find restaurants";
  }
}

function bind() {
  renderResultCounts();
  renderInitial();
  $("min-rating").addEventListener("input", (event) => {
    $("rating-readout").textContent = ratingReadout(Number(event.target.value));
  });
  $("cuisine-input").addEventListener("input", showSuggestions);
  $("cuisine-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addCuisine(event.target.value);
    } else if (event.key === "Escape") {
      hideSuggestions();
    }
  });
  $("cuisine-input").addEventListener("blur", () => {
    window.setTimeout(hideSuggestions, 150);
  });
  $("prefs").addEventListener("submit", onSubmit);
}

bind();
loadFacets().catch((error) => {
  $("location").replaceChildren(new Option("Neighborhoods unavailable", ""));
  renderError(error.message || "Could not load the catalog.");
});
