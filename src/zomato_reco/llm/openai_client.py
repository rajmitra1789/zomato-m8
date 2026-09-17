"""OpenAI-compatible client, defaulting to Groq.

SDK-level retries are disabled so timeout / 429 / parse-failure handling matches
architecture §8.4 rather than the SDK's defaults.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from zomato_reco.config import settings
from zomato_reco.llm.base import LLMError
from zomato_reco.llm.parser import loads_payload
from zomato_reco.llm.prompts import RESPONSE_SCHEMA, repair_user_message

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
    ) -> None:
        key = api_key if api_key is not None else settings.openai_api_key
        if not key or not key.strip():
            raise LLMError("No LLM API key configured")
        self._model = model or settings.llm_model
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
                return loads_payload(text)
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
                last_error = exc
                if attempt == 0:
                    wait = _retry_after_seconds(exc) or _BACKOFF_SECONDS
                    logger.warning("LLM rate-limited; retrying after %.1fs", wait)
                    time.sleep(wait)
                    continue
                raise LLMError("LLM rate-limited") from exc
            except (APITimeoutError, APIConnectionError, TimeoutError) as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning("LLM transport error; retrying once: %s", exc)
                    time.sleep(_BACKOFF_SECONDS)
                    continue
                raise LLMError("LLM request timed out") from exc
            except APIStatusError as exc:
                last_error = exc
                if attempt == 0 and exc.status_code in {429, 500, 502, 503, 529}:
                    wait = _retry_after_seconds(exc) or _BACKOFF_SECONDS
                    time.sleep(wait)
                    continue
                raise LLMError(f"LLM HTTP {exc.status_code}") from exc
        raise LLMError("LLM call failed") from last_error

    def _complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
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
                kwargs["response_format"] = {"type": "json_object"}
                response = self._client.chat.completions.create(**kwargs)
            else:
                raise

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise LLMError("LLM returned no choices")
        content = choice.message.content or ""
        if not content.strip() and getattr(choice.message, "refusal", None):
            raise LLMError("LLM refused to produce a recommendation")
        return content


def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def get_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient()
