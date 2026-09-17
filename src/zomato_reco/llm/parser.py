"""Six-step anti-hallucination gate from architecture §8.3.

The model is allowed to contribute order and prose. Every name, cuisine, rating, and cost
shown to the user is re-joined from the candidate table by id.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from zomato_reco.llm.base import LLMError
from zomato_reco.models import Recommendation

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class LLMItem(BaseModel):
    """Wire format. Extra keys (name, rating, cost, ...) are discarded here — step 6."""

    model_config = ConfigDict(extra="ignore")

    id: str
    rank: int = 0
    explanation: str = ""
    match_highlights: list[str] = Field(default_factory=list)


class LLMPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendations: list[LLMItem]
    summary: str | None = None


@dataclass
class GateResult:
    recommendations: list[Recommendation]
    summary: str | None
    dropped_ids: list[str] = field(default_factory=list)
    backfilled_ids: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [item.id for item in self.recommendations]

    @property
    def used_model_prose(self) -> bool:
        """True when at least one surviving row still has a model-written explanation."""
        backfilled = set(self.backfilled_ids)
        return any(item.id not in backfilled for item in self.recommendations)


def strip_fences(text: str) -> str:
    """gpt-oss (and most chat models) wrap JSON in markdown fences often enough to handle."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    return stripped


def loads_payload(text: str) -> dict[str, Any]:
    """Step 1: parse JSON. Raises `LLMError` so the client/pipeline can retry or fall back."""
    if not text or not text.strip():
        raise LLMError("LLM returned an empty response")
    candidate = strip_fences(text)
    # If the model prepended prose, take the outermost object.
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError("LLM response contained no JSON object")
    candidate = candidate[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMError("LLM JSON root was not an object")
    return payload


def gate(
    payload: dict[str, Any],
    *,
    candidate_ids: list[str],
    requested: int,
    fallback_explanations: dict[str, str],
    fallback_highlights: dict[str, list[str]],
) -> GateResult:
    """Steps 2–6: schema, allowlist, dedupe, backfill, discard facts.

    `candidate_ids` is the deterministic order, used both as the allowlist and as the
    backfill source. `requested` is how many results the user asked for, capped by how
    many candidates exist.
    """
    allowlist = list(dict.fromkeys(candidate_ids))
    requested = max(0, min(requested, len(allowlist)))

    try:
        parsed = LLMPayload.model_validate(payload)
    except ValidationError as exc:
        raise LLMError(f"LLM JSON failed schema validation: {exc}") from exc

    dropped: list[str] = []
    seen: set[str] = set()
    kept: list[LLMItem] = []
    for item in parsed.recommendations:
        ident = (item.id or "").strip()
        if ident not in allowlist:
            dropped.append(ident or "<empty>")
            continue
        if ident in seen:
            dropped.append(ident)
            continue
        seen.add(ident)
        kept.append(item)
        if len(kept) == requested:
            break

    backfilled: list[str] = []
    if len(kept) < requested:
        for ident in allowlist:
            if ident in seen:
                continue
            kept.append(
                LLMItem(
                    id=ident,
                    explanation=fallback_explanations.get(ident, ""),
                    match_highlights=fallback_highlights.get(ident, []),
                )
            )
            backfilled.append(ident)
            seen.add(ident)
            if len(kept) == requested:
                break

    recommendations = [
        Recommendation(
            id=item.id,
            rank=position,
            explanation=item.explanation or fallback_explanations.get(item.id, ""),
            match_highlights=item.match_highlights or fallback_highlights.get(item.id, []),
        )
        for position, item in enumerate(kept, start=1)
    ]
    return GateResult(
        recommendations=recommendations,
        summary=parsed.summary,
        dropped_ids=dropped,
        backfilled_ids=backfilled,
    )
