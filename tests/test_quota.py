"""Groq quota tracker: stay inside 30 RPM / 1k RPD / 8k TPM / 200k TPD."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zomato_reco.llm.cache import ResponseCache, cache_key
from zomato_reco.llm.openai_client import OpenAICompatibleClient
from zomato_reco.llm.prompts import RESPONSE_SCHEMA
from zomato_reco.llm.quota import QuotaExceeded, QuotaTracker, estimate_call_tokens, reset_quota


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    reset_quota()
    yield
    reset_quota()


def _tracker(clock: Clock, tmp_path, **kwargs) -> QuotaTracker:
    return QuotaTracker(
        path=tmp_path / "quota.json",
        clock=clock,
        margin=1.0,
        **kwargs,
    )


def test_rpm_blocks_the_31st_request(tmp_path):
    clock = Clock()
    quota = _tracker(clock, tmp_path, rpm=30, rpd=10_000, tpm=1_000_000, tpd=1_000_000)
    for _ in range(30):
        quota.check(1)
        quota.commit(1)
    with pytest.raises(QuotaExceeded, match="rpm"):
        quota.check(1)


def test_tpm_is_the_binding_cap(tmp_path):
    """8k tokens/minute is far tighter than 30 requests/minute."""
    clock = Clock()
    quota = _tracker(clock, tmp_path, rpm=30, rpd=10_000, tpm=8_000, tpd=200_000)
    quota.check(3_500)
    quota.commit(3_500)
    quota.check(3_500)
    quota.commit(3_500)
    with pytest.raises(QuotaExceeded, match="tpm"):
        quota.check(3_500)


def test_minute_window_slides(tmp_path):
    clock = Clock()
    quota = _tracker(clock, tmp_path, rpm=1, rpd=10_000, tpm=8_000, tpd=200_000)
    quota.commit(100)
    with pytest.raises(QuotaExceeded, match="rpm"):
        quota.check(1)
    clock.t += 61
    quota.check(1)
    quota.commit(1)


def test_daily_counters_persist_and_roll(tmp_path):
    clock = Clock()
    path = tmp_path / "quota.json"
    first = QuotaTracker(
        path=path, clock=clock, margin=1.0, rpm=30, rpd=1_000, tpm=8_000, tpd=200_000
    )
    first.commit(4_000)
    revived = QuotaTracker(
        path=path, clock=clock, margin=1.0, rpm=30, rpd=1_000, tpm=8_000, tpd=200_000
    )
    assert revived.snapshot()["tpd_used"] == 4_000
    assert revived.snapshot()["rpd_used"] == 1

    clock.t += 86_400  # next UTC day
    revived.check(4_000)
    assert revived.snapshot()["tpd_used"] == 0


def test_tpd_and_rpd_caps(tmp_path):
    clock = Clock()
    quota = _tracker(clock, tmp_path, rpm=30, rpd=2, tpm=8_000, tpd=5_000)
    quota.commit(4_000)
    with pytest.raises(QuotaExceeded, match="tpd"):
        quota.check(1_500)
    other = _tracker(clock, tmp_path / "rpd", rpm=30, rpd=1, tpm=8_000, tpd=200_000)
    other.commit(10)
    with pytest.raises(QuotaExceeded, match="rpd"):
        other.check(10)


def test_margin_tightens_published_caps(tmp_path):
    clock = Clock()
    quota = QuotaTracker(
        path=tmp_path / "q.json",
        clock=clock,
        rpm=10,
        rpd=100,
        tpm=1_000,
        tpd=10_000,
        margin=0.9,
    )
    assert quota.rpm == 9
    assert quota.tpm == 900


def test_estimate_includes_output_headroom():
    prompt = estimate_call_tokens("sys", "user")
    assert prompt > 1_500  # output estimate is added even for tiny prompts


def test_cache_hit_skips_provider_and_quota(tmp_path, monkeypatch):
    clock = Clock()
    quota = _tracker(clock, tmp_path, rpm=1, rpd=1, tpm=8_000, tpd=200_000)
    cache = ResponseCache()
    payload = {"recommendations": [], "summary": None}
    key = cache_key("openai/gpt-oss-120b", "sys", "user", RESPONSE_SCHEMA)
    cache.put(key, payload)

    client = OpenAICompatibleClient(
        api_key="gsk-test",
        quota=quota,
        cache=cache,
        model="openai/gpt-oss-120b",
    )

    def boom(*_a, **_k):
        raise AssertionError("provider must not be called on a cache hit")

    monkeypatch.setattr(client._client.chat.completions, "create", boom)
    monkeypatch.setattr("zomato_reco.llm.openai_client.settings.llm_cache", True)
    assert client.complete_json("sys", "user") == payload
    assert quota.snapshot()["rpm_used"] == 0


def test_client_checks_quota_before_http(tmp_path, monkeypatch):
    clock = Clock()
    quota = _tracker(clock, tmp_path, rpm=1, rpd=10, tpm=100, tpd=1_000)
    quota.commit(100)
    client = OpenAICompatibleClient(
        api_key="gsk-test",
        quota=quota,
        cache=ResponseCache(),
        model="openai/gpt-oss-120b",
    )
    called = {"n": 0}

    def create(**_k):
        called["n"] += 1
        return SimpleNamespace(choices=[], usage=None)

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    monkeypatch.setattr("zomato_reco.llm.openai_client.settings.llm_cache", False)
    with pytest.raises(QuotaExceeded):
        client.complete_json("system prompt here", "user prompt here", {})
    assert called["n"] == 0
