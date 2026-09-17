"""OpenAI-compatible client, defaulting to Groq.

SDK-level retries are disabled so timeout / 429 / parse-failure handling matches
architecture §8.4 rather than the SDK's defaults. Client-side quota is checked
*before* each HTTP call so we do not spend the 8k tokens/minute budget on a retry
that Groq would reject anyway.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from zomato_reco.config import settings
from zomato_reco.llm.base import LLMError
from zomato_reco.llm.cache import cache_key, get_cache
from zomato_reco.llm.parser import loads_payload
from zomato_reco.llm.prompts import RESPONSE_SCHEMA, repair_user_message
from zomato_reco.llm.quota import QuotaExceeded, QuotaTracker, estimate_call_tokens, get_quota

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = 1.0


class OpenAICompatibleClient:
    """Concrete `LLMClient`. Named for the SDK, not the host — Groq is the default host."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        quota: QuotaTracker | None = None,
        cache: Any | None = None,
    ) -> None:
        key = api_key if api_key is not None else settings.openai_api_key
        if not key or not key.strip():
            raise LLMError("No LLM API key configured")
        self._model = model or settings.llm_model
        self._quota = quota if quota is not None else get_quota()
        self._cache = cache if cache is not None else get_cache()
        self._client = OpenAI(
            api_key=key.strip(),
            base_url=base_url or settings.openai_base_url,
            timeout=timeout if timeout is not None else settings.llm_timeout_seconds,
            max_retries=0,
        )

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        schema = schema or RESPONSE_SCHEMA
        if settings.llm_cache:
            key = cache_key(self._model, system, user, schema)
            cached = self._cache.get(key)
            if cached is not None:
                logger.info("LLM cache hit; quota untouched")
                return cached
        else:
            key = None

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None
        original = list(messages)
        text = ""
        for attempt in range(2):
            try:
                text = self._complete(messages, schema)
                payload = loads_payload(text)
                if key is not None:
                    self._cache.put(key, payload)
                return payload
            except QuotaExceeded:
                raise
            except LLMError as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning("LLM JSON parse failed; retrying once: %s", exc)
                    messages = [
                        *original,
                        {"role": "assistant", "content": text[:2000]},
                        {"role": "user", "content": repair_user_message(str(exc))},
                    ]
                    continue
                raise
            except RateLimitError as exc:
                # Do not retry a 429: it costs another request against 30 RPM / 8k TPM
                # and the local quota should have stopped us. Fall back immediately.
                raise LLMError("LLM rate-limited by provider") from exc
            except (APITimeoutError, APIConnectionError, TimeoutError) as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning("LLM transport error; retrying once: %s", exc)
                    time.sleep(_BACKOFF_SECONDS)
                    continue
                raise LLMError("LLM request timed out") from exc
            except APIStatusError as exc:
                last_error = exc
                if attempt == 0 and exc.status_code in {500, 502, 503, 529}:
                    time.sleep(_BACKOFF_SECONDS)
                    continue
                raise LLMError(f"LLM HTTP {exc.status_code}") from exc
        raise LLMError("LLM call failed") from last_error

    def _complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        estimated = estimate_call_tokens(system, user)
        self._quota.check(estimated)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "recommendation_result",
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        if "gpt-oss" in self._model:
            kwargs["extra_body"] = {"reasoning_effort": settings.reasoning_effort}

        try:
            response = self._client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            # Groq json_schema is supported on gpt-oss; other hosts may only have json_object.
            if exc.status_code == 400:
                logger.info("json_schema rejected; retrying with json_object mode")
                self._quota.commit(0)
                self._quota.check(estimated)
                kwargs["response_format"] = {"type": "json_object"}
                try:
                    response = self._client.chat.completions.create(**kwargs)
                except Exception:
                    self._quota.commit(0)
                    raise
            else:
                self._quota.commit(0)
                raise
        except Exception:
            self._quota.commit(0)
            raise

        used = _usage_tokens(response, fallback=estimated)
        self._quota.commit(used)

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise LLMError("LLM returned no choices")
        content = choice.message.content or ""
        if not content.strip() and getattr(choice.message, "refusal", None):
            raise LLMError("LLM refused to produce a recommendation")
        return content


def _usage_tokens(response: Any, *, fallback: int) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return fallback
    total = getattr(usage, "total_tokens", None)
    if isinstance(total, int) and total > 0:
        return total
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    combined = int(prompt) + int(completion)
    return combined if combined > 0 else fallback


def get_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient()
