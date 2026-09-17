"""Client-side Groq quota so we never send a request that would 429.

Published free-tier caps for `openai/gpt-oss-120b`:
  30 requests/minute, 1,000 requests/day, 8,000 tokens/minute, 200,000 tokens/day.

The 8k TPM cap is the binding one: a typical ranking call is ~2–4k tokens, so a few
searches in a minute would trip Groq if we did not stop first. When a cap would be
hit, we raise `QuotaExceeded` and the pipeline falls back to deterministic ranking
instead of waiting or retrying.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from zomato_reco.config import settings
from zomato_reco.llm.base import LLMError

logger = logging.getLogger(__name__)

Clock = Callable[[], float]


class QuotaExceeded(LLMError):
    """A local cap would be breached; do not call the provider."""

    def __init__(self, cap: str, detail: str) -> None:
        self.cap = cap
        super().__init__(f"LLM quota exceeded ({cap}): {detail}")


@dataclass
class _Event:
    at: float
    tokens: int


class QuotaTracker:
    """Sliding one-minute window plus a persisted UTC-day counter."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        clock: Clock | None = None,
        rpm: int | None = None,
        rpd: int | None = None,
        tpm: int | None = None,
        tpd: int | None = None,
        margin: float | None = None,
    ) -> None:
        self._path = path if path is not None else settings.llm_quota_path
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._minute: list[_Event] = []
        self._day = ""
        self._day_requests = 0
        self._day_tokens = 0
        margin = settings.llm_quota_margin if margin is None else margin
        self.rpm = int((rpm if rpm is not None else settings.llm_rpm) * margin)
        self.rpd = int((rpd if rpd is not None else settings.llm_rpd) * margin)
        self.tpm = int((tpm if tpm is not None else settings.llm_tpm) * margin)
        self.tpd = int((tpd if tpd is not None else settings.llm_tpd) * margin)
        self._load()

    def check(self, estimated_tokens: int) -> None:
        """Raise `QuotaExceeded` if recording this call would breach a cap."""
        with self._lock:
            self._roll()
            minute_requests = len(self._minute)
            minute_tokens = sum(event.tokens for event in self._minute)
            self._would_exceed(
                requests=1,
                tokens=max(0, estimated_tokens),
                minute_requests=minute_requests,
                minute_tokens=minute_tokens,
            )

    def commit(self, tokens: int) -> None:
        """Record a completed (or attempted) provider call."""
        used = max(0, int(tokens))
        with self._lock:
            self._roll()
            now = self._clock()
            self._minute.append(_Event(at=now, tokens=used))
            self._day_requests += 1
            self._day_tokens += used
            self._save()
        logger.info(
            "LLM quota: %s/%s rpm, %s/%s tpm, %s/%s rpd, %s/%s tpd",
            len(self._minute),
            self.rpm,
            sum(e.tokens for e in self._minute),
            self.tpm,
            self._day_requests,
            self.rpd,
            self._day_tokens,
            self.tpd,
        )

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            self._roll()
            return {
                "rpm_used": len(self._minute),
                "rpm_limit": self.rpm,
                "tpm_used": sum(e.tokens for e in self._minute),
                "tpm_limit": self.tpm,
                "rpd_used": self._day_requests,
                "rpd_limit": self.rpd,
                "tpd_used": self._day_tokens,
                "tpd_limit": self.tpd,
            }

    def _would_exceed(
        self,
        *,
        requests: int,
        tokens: int,
        minute_requests: int,
        minute_tokens: int,
    ) -> None:
        if minute_requests + requests > self.rpm:
            raise QuotaExceeded(
                "rpm",
                f"{minute_requests + requests} requests in 60s, cap {self.rpm}",
            )
        if minute_tokens + tokens > self.tpm:
            raise QuotaExceeded(
                "tpm",
                f"{minute_tokens + tokens} tokens in 60s, cap {self.tpm}",
            )
        if self._day_requests + requests > self.rpd:
            raise QuotaExceeded(
                "rpd",
                f"{self._day_requests + requests} requests today, cap {self.rpd}",
            )
        if self._day_tokens + tokens > self.tpd:
            raise QuotaExceeded(
                "tpd",
                f"{self._day_tokens + tokens} tokens today, cap {self.tpd}",
            )

    def _roll(self) -> None:
        now = self._clock()
        cutoff = now - 60.0
        self._minute = [event for event in self._minute if event.at > cutoff]
        today = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._day_requests = 0
            self._day_tokens = 0

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            return
        today = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y-%m-%d")
        if raw.get("day") == today:
            self._day = today
            self._day_requests = int(raw.get("requests", 0))
            self._day_tokens = int(raw.get("tokens", 0))

    def _save(self) -> None:
        payload = {
            "day": self._day,
            "requests": self._day_requests,
            "tokens": self._day_tokens,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self._path)
        except OSError:
            logger.warning("Could not persist LLM quota to %s", self._path)


_tracker: QuotaTracker | None = None
_tracker_lock = threading.Lock()


def get_quota() -> QuotaTracker:
    global _tracker
    with _tracker_lock:
        if _tracker is None:
            _tracker = QuotaTracker()
        return _tracker


def reset_quota() -> None:
    """Test helper: drop the process singleton."""
    global _tracker
    with _tracker_lock:
        _tracker = None


def estimate_call_tokens(system: str, user: str) -> int:
    """Conservative pre-flight size: prompt estimate plus typical completion."""
    from zomato_reco.llm.prompts import estimate_tokens

    prompt = estimate_tokens(system) + estimate_tokens(user)
    return prompt + settings.llm_output_token_estimate
