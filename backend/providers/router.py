"""Ordered failover across providers, with a circuit breaker and full logging.

This is the heart of the project. The rules it encodes are not arbitrary - each
one comes from a failure actually observed while probing the real APIs:

- Never retry a 429. Free tiers have hard daily caps; a retry loop spends the
  quota we are trying to protect.
- Never retry a 401/403/402. Those are configuration, not weather.
- A dead model ID should cost us one model, not one provider: try that
  provider's fallback models before moving on.
- A 200 response can still be a failure (empty content from a reasoning model).
- Two consecutive failures bench a provider for a cooldown window, so a
  provider having a bad minute does not slow every subsequent request.

Note on logging: only real network attempts are written to `provider_logs`.
Skips (no key, cooling down) are reported in the returned `attempts` list and by
`status()`, but are deliberately not logged as attempts - counting them would
make the success-rate column meaningless.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from .. import db
from ..config import Settings, settings as default_settings
from .base import (AuthFailed, PaymentRequired, Provider, ProviderError,
                   RateLimited, close_http_client)
from .cohere import CohereProvider
from .huggingface import HuggingFaceProvider
from .nvidia import NvidiaProvider
from .openrouter import OpenRouterProvider

PROVIDER_CLASSES = {
    "huggingface": HuggingFaceProvider,
    "cohere": CohereProvider,
    "nvidia": NvidiaProvider,
    "openrouter": OpenRouterProvider,
}

# Errors that mean "this provider is done for now" - no other model will help.
_ABANDON_PROVIDER = (RateLimited, AuthFailed, PaymentRequired)


@dataclass
class Attempt:
    """One thing the router tried, successful or not."""

    provider: str
    model: str | None
    success: bool
    latency_ms: int
    error: str | None = None
    skipped: bool = False
    log_id: int | None = None


@dataclass
class RouterResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def log_ids(self) -> list[int]:
        return [a.log_id for a in self.attempts if a.log_id is not None]


class AllProvidersFailed(Exception):
    """Every configured provider failed.

    The router's job ends here. The orchestrator catches this and switches to
    the deterministic local analyzer, which keeps the failover policy (here) and
    the degradation policy (there) in separate places.
    """

    def __init__(self, attempts: list[Attempt]):
        self.attempts = attempts
        tried = ", ".join(
            f"{a.provider}({a.error or 'skipped'})" for a in attempts
        ) or "none configured"
        super().__init__(f"all providers failed: {tried}")

    @property
    def log_ids(self) -> list[int]:
        return [a.log_id for a in self.attempts if a.log_id is not None]


class CircuitBreaker:
    """Benches a provider after consecutive failures.

    Deliberately in-memory: a process restart should give every provider a fresh
    chance rather than inheriting a stale grudge.
    """

    def __init__(self, failure_threshold: int = 2, cooldown_seconds: int = 60):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._failures: dict[str, int] = {}
        self._cooling_until: dict[str, float] = {}

    def _now(self) -> float:
        return time.monotonic()

    def is_cooling(self, name: str) -> bool:
        until = self._cooling_until.get(name)
        if until is None:
            return False
        if self._now() >= until:
            # Window expired - clear it so the provider gets a clean attempt.
            self._cooling_until.pop(name, None)
            self._failures[name] = 0
            return False
        return True

    def seconds_left(self, name: str) -> int:
        until = self._cooling_until.get(name)
        if until is None:
            return 0
        return max(0, int(round(until - self._now())))

    def record_success(self, name: str) -> None:
        self._failures[name] = 0
        self._cooling_until.pop(name, None)

    def record_failure(self, name: str) -> None:
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        if count >= self.failure_threshold:
            self._cooling_until[name] = self._now() + self.cooldown_seconds

    def failures(self, name: str) -> int:
        return self._failures.get(name, 0)

    def reset(self) -> None:
        self._failures.clear()
        self._cooling_until.clear()


class ProviderRouter:
    def __init__(
        self,
        settings: Settings | None = None,
        providers: Sequence[Provider] | None = None,
        breaker: CircuitBreaker | None = None,
        *,
        log_attempts: bool = True,
    ):
        self.settings = settings or default_settings
        self.providers: list[Provider] = list(
            providers if providers is not None else self._build_providers()
        )
        self.breaker = breaker or CircuitBreaker(
            self.settings.breaker_failure_threshold,
            self.settings.breaker_cooldown_seconds,
        )
        self.log_attempts = log_attempts

    def _build_providers(self) -> list[Provider]:
        """Instantiate providers in PROVIDER_ORDER."""
        built: list[Provider] = []
        for name in self.settings.provider_order:
            cls = PROVIDER_CLASSES.get(name)
            if cls is None:
                continue
            built.append(cls(self.settings.provider(name), self.settings))
        return built

    # ---------------------------------------------------------------- logging
    def _log(self, attempt: Attempt) -> None:
        if not self.log_attempts or attempt.skipped:
            return
        try:
            attempt.log_id = db.log_provider_attempt(
                provider=attempt.provider,
                model=attempt.model,
                success=attempt.success,
                latency_ms=attempt.latency_ms,
                error=attempt.error,
            )
        except Exception:
            # Observability must never take down the request it observes.
            attempt.log_id = None

    # --------------------------------------------------------------- failover
    def generate(
        self, system_prompt: str, user_prompt: str, *, json_mode: bool = True
    ) -> RouterResult:
        attempts: list[Attempt] = []

        for provider in self.providers:
            if not provider.is_configured:
                attempts.append(Attempt(provider.name, None, False, 0,
                                        "no_key", skipped=True))
                continue

            if self.breaker.is_cooling(provider.name):
                attempts.append(Attempt(
                    provider.name, provider.models[0], False, 0,
                    f"cooling_down:{self.breaker.seconds_left(provider.name)}s",
                    skipped=True,
                ))
                continue

            abandon = False
            for model in provider.models:
                # At most two tries per model, and only when the error class
                # says a retry could plausibly help.
                for attempt_no in (1, 2):
                    started = time.monotonic()
                    try:
                        text = provider.generate(
                            system_prompt, user_prompt, model=model, json_mode=json_mode
                        )
                    except ProviderError as exc:
                        elapsed = int((time.monotonic() - started) * 1000)
                        record = Attempt(provider.name, model, False, elapsed, str(exc))
                        self._log(record)
                        attempts.append(record)
                        self.breaker.record_failure(provider.name)

                        if isinstance(exc, _ABANDON_PROVIDER):
                            abandon = True
                            break
                        if not exc.quick_retryable or attempt_no == 2:
                            break  # move to this provider's next model
                        continue

                    elapsed = int((time.monotonic() - started) * 1000)
                    record = Attempt(provider.name, model, True, elapsed)
                    self._log(record)
                    attempts.append(record)
                    self.breaker.record_success(provider.name)
                    return RouterResult(
                        text=text, provider=provider.name, model=model,
                        latency_ms=elapsed, attempts=attempts,
                    )
                if abandon:
                    break

        raise AllProvidersFailed(attempts)

    # ----------------------------------------------------------------- status
    def status(self) -> list[dict]:
        """Per-provider state for GET /providers/status.

        Reports booleans and aggregates only - never a key, never a prefix.
        """
        stats = {}
        try:
            stats = db.provider_stats()
        except Exception:
            stats = {}

        out: list[dict] = []
        for provider in self.providers:
            provider_stats = stats.get(provider.name, {})
            out.append({
                "provider": provider.name,
                "model": provider.models[0] if provider.models else None,
                "fallback_models": list(provider.models[1:]),
                "configured": provider.is_configured,
                "supports_json_mode": provider.supports_json_mode,
                "cooling_down": self.breaker.is_cooling(provider.name),
                "cooling_seconds_left": self.breaker.seconds_left(provider.name),
                "consecutive_failures": self.breaker.failures(provider.name),
                "attempts": provider_stats.get("attempts", 0),
                "success_rate": provider_stats.get("success_rate"),
                "avg_latency_ms": provider_stats.get("avg_latency_ms"),
            })
        return out

    def close(self) -> None:
        close_http_client()
