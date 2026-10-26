"""Configuration loaded from the environment.

Secrets live in `.env` and nowhere else. Every object in this module masks its
secrets in `__repr__`, so an accidental `print(settings)` or a logged exception
can never expose a key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# override=False so a real environment variable always beats the .env file.
load_dotenv(ROOT / ".env", override=False)

VERSION = "1.0.0"

# Provider names in the order they are defined. PROVIDER_ORDER may list any
# subset; unknown names are ignored rather than raising, so a typo in .env
# degrades to "that provider is skipped" instead of crashing the app.
KNOWN_PROVIDERS = ("huggingface", "cohere", "nvidia", "openrouter")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in _env(name).split(",") if item.strip())


def _mask(secret: str) -> str:
    """Never reveal a key. Report only whether one is present and how long."""
    return f"<set:{len(secret)}chars>" if secret else "<unset>"


@dataclass(frozen=True)
class ProviderConfig:
    """One provider's credentials and model preferences."""

    name: str
    api_key: str
    model: str
    fallback_models: tuple[str, ...] = ()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    @property
    def models(self) -> tuple[str, ...]:
        """Primary model first, then declared fallbacks, de-duplicated.

        These are tried in order *within* this provider before the router moves
        on, so one deprecated model ID does not take the whole provider down.
        """
        seen: list[str] = []
        for model in (self.model, *self.fallback_models):
            if model and model not in seen:
                seen.append(model)
        return tuple(seen)

    def __repr__(self) -> str:  # pragma: no cover - defensive only
        return (
            f"ProviderConfig(name={self.name!r}, api_key={_mask(self.api_key)}, "
            f"model={self.model!r}, fallback_models={self.fallback_models!r})"
        )


@dataclass(frozen=True)
class Settings:
    provider_order: tuple[str, ...]
    providers: dict[str, ProviderConfig]
    request_timeout: int
    max_output_tokens: int
    max_input_chars: int
    breaker_failure_threshold: int
    breaker_cooldown_seconds: int
    backend_host: str
    backend_port: int
    db_path: Path
    version: str = VERSION

    @property
    def configured_providers(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.provider_order
            if self.providers[name].is_configured
        )

    def provider(self, name: str) -> ProviderConfig:
        return self.providers[name]

    def __repr__(self) -> str:  # pragma: no cover - defensive only
        return (
            f"Settings(provider_order={self.provider_order!r}, "
            f"configured={self.configured_providers!r}, "
            f"request_timeout={self.request_timeout}, "
            f"max_output_tokens={self.max_output_tokens}, "
            f"max_input_chars={self.max_input_chars}, "
            f"db_path={self.db_path!r})"
        )


def _provider_configs() -> dict[str, ProviderConfig]:
    """Map each provider to its env vars.

    Hugging Face uses HF_TOKEN rather than *_API_KEY because that is the name
    the HF tooling itself uses; accepting HF_API_KEY too avoids a confusing
    silent skip if someone writes the name they expected.
    """
    specs = {
        "huggingface": (_env("HF_TOKEN") or _env("HF_API_KEY"), "HF_MODEL", "HF_FALLBACK_MODELS"),
        "cohere": (_env("COHERE_API_KEY"), "COHERE_MODEL", "COHERE_FALLBACK_MODELS"),
        "nvidia": (_env("NVIDIA_API_KEY"), "NVIDIA_MODEL", "NVIDIA_FALLBACK_MODELS"),
        "openrouter": (
            _env("OPENROUTER_API_KEY"),
            "OPENROUTER_MODEL",
            "OPENROUTER_FALLBACK_MODELS",
        ),
    }
    return {
        name: ProviderConfig(
            name=name,
            api_key=key,
            model=_env(model_var),
            fallback_models=_env_list(fallback_var),
        )
        for name, (key, model_var, fallback_var) in specs.items()
    }


def load_settings() -> Settings:
    order = _env_list("PROVIDER_ORDER") or KNOWN_PROVIDERS
    # Keep only names we actually implement, then append any implemented
    # provider the user forgot to list - it can still serve as a last resort.
    ordered = [name for name in order if name in KNOWN_PROVIDERS]
    ordered += [name for name in KNOWN_PROVIDERS if name not in ordered]

    db_path = ROOT / "data" / "resumematch.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        provider_order=tuple(ordered),
        providers=_provider_configs(),
        request_timeout=_env_int("REQUEST_TIMEOUT_SECONDS", 45),
        max_output_tokens=_env_int("MAX_OUTPUT_TOKENS", 2000),
        max_input_chars=_env_int("MAX_INPUT_CHARS", 15000),
        breaker_failure_threshold=_env_int("BREAKER_FAILURE_THRESHOLD", 2),
        breaker_cooldown_seconds=_env_int("BREAKER_COOLDOWN_SECONDS", 60),
        backend_host=_env("BACKEND_HOST", "127.0.0.1"),
        backend_port=_env_int("BACKEND_PORT", 8000),
        db_path=db_path,
    )


settings = load_settings()
