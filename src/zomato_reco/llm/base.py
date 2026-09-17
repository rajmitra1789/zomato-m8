"""LLM client protocol. Pipeline code depends on this, never on Groq or OpenAI directly."""

from typing import Any, Protocol


class LLMError(Exception):
    """Any LLM failure the pipeline should catch and degrade from.

    Timeouts, rate limits, malformed JSON after retry, and transport errors all become
    this so `recommend()` has one fallback path instead of a zoo of except clauses.
    """


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a parsed JSON object. Raise `LLMError` rather than a provider exception."""
        ...
