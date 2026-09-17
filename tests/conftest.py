"""Keep the suite keyless even if a developer has GROQ_API_KEY in `.env`.

The Phase 5 contract is that CI never needs a key. An autouse patch here means a local
`.env` cannot accidentally turn `recommend()` into a live Groq call during tests.
"""

import pytest

from zomato_reco.config import settings


@pytest.fixture(autouse=True)
def _disable_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
