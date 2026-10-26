"""Failover, circuit breaker and local fallback. Mocked providers only."""

from __future__ import annotations

import pytest

from backend.providers.base import (AuthFailed, EmptyResponse, ModelNotFound,
                                    PaymentRequired, RateLimited, ServerError)
from backend.providers.router import (AllProvidersFailed, CircuitBreaker,
                                      ProviderRouter)


def route(settings, providers, breaker=None):
    return ProviderRouter(settings, providers=providers, breaker=breaker,
                          log_attempts=False)


def test_first_success_stops_the_chain(settings, fake):
    a = fake("alpha", settings, responses=['{"n":1}'])
    b = fake("bravo", settings, responses=['{"n":2}'])
    result = route(settings, [a, b]).generate("s", "u")

    assert result.provider == "alpha"
    assert result.text == '{"n":1}'
    assert len(a.calls) == 1
    assert b.calls == []        # never touched


def test_order_is_respected_on_failover(settings, fake):
    a = fake("alpha", settings, responses=[RateLimited("quota")])
    b = fake("bravo", settings, responses=['{"n":2}'])
    c = fake("charlie", settings, responses=['{"n":3}'])
    result = route(settings, [a, b, c]).generate("s", "u")

    assert result.provider == "bravo"
    assert c.calls == []


@pytest.mark.parametrize("error", [
    RateLimited("429"), AuthFailed("401"), PaymentRequired("402"),
])
def test_terminal_errors_are_never_retried(settings, fake, error):
    """Retrying a 429 spends the very quota we are trying to protect."""
    a = fake("alpha", settings, responses=[error, '{"n":1}'])
    b = fake("bravo", settings, responses=['{"n":2}'])
    result = route(settings, [a, b]).generate("s", "u")

    assert len(a.calls) == 1
    assert result.provider == "bravo"


@pytest.mark.parametrize("error", [ServerError("500"), EmptyResponse("blank")])
def test_transient_errors_get_exactly_one_retry(settings, fake, error):
    a = fake("alpha", settings, responses=[error, error, '{"n":1}'])
    b = fake("bravo", settings, responses=['{"n":2}'])
    result = route(settings, [a, b]).generate("s", "u")

    assert len(a.calls) == 2    # one try, one retry - never three
    assert result.provider == "bravo"


def test_transient_error_then_success_on_retry(settings, fake):
    a = fake("alpha", settings, responses=[ServerError("500"), '{"n":1}'])
    result = route(settings, [a]).generate("s", "u")

    assert result.provider == "alpha"
    assert len(a.calls) == 2


def test_unconfigured_provider_is_skipped(settings, fake):
    a = fake("alpha", settings, key="", responses=['{"n":1}'])
    b = fake("bravo", settings, responses=['{"n":2}'])
    result = route(settings, [a, b]).generate("s", "u")

    assert a.calls == []
    assert result.provider == "bravo"
    skips = [x for x in result.attempts if x.skipped and x.error == "no_key"]
    assert len(skips) == 1


def test_dead_model_falls_back_within_the_provider(settings, fake):
    """One stale model ID should cost one model, not the whole provider."""
    class DeadFirst(type(fake("x", settings))):
        def generate(self, system_prompt, user_prompt, *, model=None, json_mode=True):
            self.calls.append(model)
            if model == "dead":
                raise ModelNotFound("410 gone")
            return '{"n":"second"}'

    a = DeadFirst("alpha", settings, models=("dead", "alive"))
    result = route(settings, [a]).generate("s", "u")

    assert result.model == "alive"
    assert a.calls == ["dead", "alive"]


def test_breaker_benches_after_two_consecutive_failures(settings, fake):
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    a = fake("alpha", settings, responses=[ServerError("500")])
    b = fake("bravo", settings, responses=['{"n":2}'])
    router = route(settings, [a, b], breaker)

    router.generate("s", "u")               # alpha fails twice -> benched
    assert breaker.is_cooling("alpha")
    assert 0 < breaker.seconds_left("alpha") <= 60

    calls_before = len(a.calls)
    result = router.generate("s", "u")      # alpha must be skipped entirely
    assert len(a.calls) == calls_before
    assert result.provider == "bravo"
    assert any(x.skipped and "cooling_down" in (x.error or "")
               for x in result.attempts)


def test_success_resets_the_failure_counter(settings, fake):
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    a = fake("alpha", settings, responses=[ServerError("500"), '{"n":1}'])
    route(settings, [a], breaker).generate("s", "u")

    assert not breaker.is_cooling("alpha")
    assert breaker.failures("alpha") == 0


def test_cooldown_expiry_lets_the_provider_back_in(settings, fake, monkeypatch):
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    clock = {"t": 1000.0}
    monkeypatch.setattr(breaker, "_now", lambda: clock["t"])

    breaker.record_failure("alpha")
    assert breaker.is_cooling("alpha")

    clock["t"] += 61
    assert not breaker.is_cooling("alpha")
    assert breaker.failures("alpha") == 0


def test_all_providers_failed_carries_every_attempt(settings, fake):
    a = fake("alpha", settings, responses=[RateLimited("429")])
    b = fake("bravo", settings, responses=[AuthFailed("401")])
    c = fake("charlie", settings, key="", responses=['{"n":3}'])

    with pytest.raises(AllProvidersFailed) as excinfo:
        route(settings, [a, b, c]).generate("s", "u")

    attempts = excinfo.value.attempts
    assert [x.provider for x in attempts] == ["alpha", "bravo", "charlie"]
    assert attempts[-1].skipped


def test_no_providers_configured_raises_rather_than_hangs(settings, fake):
    a = fake("alpha", settings, key="")
    with pytest.raises(AllProvidersFailed):
        route(settings, [a]).generate("s", "u")


def test_every_real_attempt_is_logged(settings, fake, memdb):
    """Skips are deliberately not logged - they would skew the success rate."""
    from backend import db

    a = fake("alpha", settings, responses=[ServerError("500")])
    b = fake("bravo", settings, responses=['{"n":2}'])
    c = fake("charlie", settings, key="")
    ProviderRouter(settings, providers=[c, a, b],
                   breaker=CircuitBreaker(9, 60)).generate("s", "u")

    rows = db.recent_provider_logs(20)
    assert [r["provider"] for r in rows if r["provider"] == "charlie"] == []
    assert len([r for r in rows if r["provider"] == "alpha"]) == 2
    success = [r for r in rows if r["provider"] == "bravo"]
    assert len(success) == 1 and success[0]["success"] == 1
    assert all(r["error"] is None or "sk-" not in r["error"] for r in rows)


def test_status_never_exposes_a_credential(settings, fake):
    a = fake("alpha", settings, key="sk-super-secret-value")
    rows = route(settings, [a]).status()

    assert rows[0]["configured"] is True
    assert "sk-super-secret-value" not in repr(rows)
