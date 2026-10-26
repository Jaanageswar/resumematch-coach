"""OpenRouter.

Free models carry a ":free" suffix and rotate often, so the model ID lives in
`.env` and is re-verified with `scripts/check_providers.py --discover`.

Measured behaviour (2026-10-23): works, but returns 429 after only a handful of
calls, which is why it sits third in PROVIDER_ORDER rather than first.
"""

from __future__ import annotations

from typing import Any

from .base import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"
    supports_json_mode = True

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        # OpenRouter uses these for attribution on its dashboard. They are
        # optional, and deliberately identify the app rather than the user.
        headers["HTTP-Referer"] = "http://localhost:5173"
        headers["X-Title"] = "ResumeMatch Coach"
        return headers

    def _payload(self, model: str, system_prompt: str, user_prompt: str,
                 json_mode: bool, extras: bool = True) -> dict[str, Any]:
        payload = super()._payload(model, system_prompt, user_prompt, json_mode, extras)
        # Never let OpenRouter silently bill a paid model when we asked for a
        # free one: a ":free" request that cannot be served should fail and let
        # the router move on.
        if extras and model.endswith(":free"):
            payload["provider"] = {"allow_fallbacks": False}
        return payload
