"""Provider interface and the error taxonomy the router routes on.

Everything outside this package calls exactly one method:

    provider.generate(system_prompt, user_prompt) -> str

The error classes matter as much as the interface. The router needs to tell
"this will never work, move on" (a bad key) apart from "try once more" (a cold
start on a free tier), because retrying the wrong one burns a daily quota.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import ProviderConfig, Settings

# How much of an upstream error body to keep. Bounded so a huge HTML error page
# cannot bloat the log table, and so nothing long enough to embed a credential
# is ever stored.
ERROR_EXCERPT = 160


class ProviderError(Exception):
    """Base failure. `quick_retryable` decides whether one retry is worthwhile."""

    quick_retryable: bool = False
    reason: str = "error"

    def __init__(self, message: str, *, reason: str | None = None):
        super().__init__(message)
        if reason:
            self.reason = reason

    def __str__(self) -> str:
        return f"{self.reason}: {super().__str__()}"


class AuthFailed(ProviderError):
    """401/403. A config problem - no amount of retrying fixes it."""

    reason = "auth_failed"


class RateLimited(ProviderError):
    """429. Retrying is actively harmful: it spends quota we do not have."""

    reason = "rate_limited"


class PaymentRequired(ProviderError):
    """402. Out of credits; will not recover today."""

    reason = "payment_required"


class ModelNotFound(ProviderError):
    """404/410. A stale or unentitled model ID - try this provider's next model."""

    reason = "model_not_found"


class BadRequest(ProviderError):
    """400. Usually an unsupported parameter, e.g. response_format."""

    reason = "bad_request"


class ServerError(ProviderError):
    reason = "server_error"
    quick_retryable = True


class ProviderTimeout(ProviderError):
    reason = "timeout"
    quick_retryable = True


class NetworkError(ProviderError):
    reason = "network_error"
    quick_retryable = True


class EmptyResponse(ProviderError):
    """HTTP 200 with no usable text.

    Not theoretical: NVIDIA reasoning models return 200 with content=null when
    the token budget is consumed by hidden reasoning tokens before any content
    is emitted. See DESIGN.md s3, finding 2.
    """

    reason = "empty_response"
    quick_retryable = True


class BadResponseShape(ProviderError):
    reason = "bad_response_shape"
    quick_retryable = True


def error_for_status(status: int, body: str) -> ProviderError:
    """Map an HTTP status onto the taxonomy above."""
    excerpt = _excerpt(body)
    if status in (401, 403):
        return AuthFailed(excerpt)
    if status == 402:
        return PaymentRequired(excerpt)
    if status == 429:
        return RateLimited(excerpt)
    if status in (404, 410):
        return ModelNotFound(excerpt)
    if status in (400, 422):
        # 422 from Cohere is "No valid response generated. Try updating
        # messages" - a request-shape complaint, so it degrades like a 400.
        return BadRequest(excerpt)
    if status >= 500:
        return ServerError(f"HTTP {status}: {excerpt}")
    return ProviderError(f"HTTP {status}: {excerpt}", reason=f"http_{status}")


def _excerpt(body: str) -> str:
    """Readable one-liner from an upstream error body, length-bounded."""
    try:
        parsed = json.loads(body)
    except Exception:
        return body.strip()[:ERROR_EXCERPT] or "no body"

    if isinstance(parsed, dict):
        for key in ("message", "detail", "error", "title"):
            value = parsed.get(key)
            if isinstance(value, dict):
                value = value.get("message") or json.dumps(value)
            if value:
                return str(value)[:ERROR_EXCERPT]
    return json.dumps(parsed)[:ERROR_EXCERPT]


_CLIENT: httpx.Client | None = None


def http_client() -> httpx.Client:
    """One shared connection pool for every provider."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(follow_redirects=False)
    return _CLIENT


def close_http_client() -> None:
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
        _CLIENT = None


class Provider(ABC):
    """One LLM endpoint. Subclasses supply the URL, payload and extraction."""

    name: str = "provider"
    url: str = ""
    supports_json_mode: bool = True

    def __init__(self, config: ProviderConfig, settings: Settings):
        self.config = config
        self.settings = settings

    # -- capability -------------------------------------------------------- #
    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    @property
    def models(self) -> tuple[str, ...]:
        return self.config.models

    # -- subclass hooks ---------------------------------------------------- #
    @abstractmethod
    def _payload(self, model: str, system_prompt: str, user_prompt: str,
                 json_mode: bool, extras: bool) -> dict[str, Any]:
        """Build the request body.

        `extras` covers provider-specific optional parameters (Cohere's
        `thinking`, OpenRouter's `provider` block). They improve speed or
        quality but an unexpected model may reject them, so `generate()` can
        turn them off and try again.
        """
        ...

    @abstractmethod
    def _extract(self, body: dict[str, Any]) -> str:
        ...

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- the one public method --------------------------------------------- #
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        json_mode: bool = True,
    ) -> str:
        """Call the provider once and return its text.

        Raises a ProviderError subclass on any failure; the router decides what
        to do about it.

        A `400`/`422` means the model rejected the request shape rather than the
        content, so we degrade the optional parts of the payload and try again
        instead of writing the provider off:

            1. everything on (JSON mode + provider extras)
            2. drop the provider extras, keep JSON mode
            3. drop JSON mode too - plain text, parsed by the JSON ladder

        Some free models reject `response_format` outright and would otherwise
        look broken when they are merely limited.
        """
        target = model or (self.models[0] if self.models else "")
        if not target:
            raise ProviderError("no model configured", reason="no_model")

        want_json = json_mode and self.supports_json_mode
        ladder = [(want_json, True), (want_json, False), (False, False)]
        # Drop duplicate rungs (e.g. when JSON mode was already off).
        ladder = list(dict.fromkeys(ladder))

        last: BadRequest | None = None
        for use_json, use_extras in ladder:
            try:
                return self._call(target, system_prompt, user_prompt,
                                  use_json, use_extras)
            except BadRequest as exc:
                last = exc
                continue
        raise last or BadRequest("request rejected at every degradation level")

    def _call(self, model: str, system_prompt: str, user_prompt: str,
              json_mode: bool, extras: bool = True) -> str:
        payload = self._payload(model, system_prompt, user_prompt, json_mode, extras)
        try:
            response = http_client().post(
                self.url,
                headers=self._headers(),
                json=payload,
                timeout=self.settings.request_timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"after {self.settings.request_timeout}s") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(str(exc)[:ERROR_EXCERPT]) from exc

        if response.status_code != 200:
            raise error_for_status(response.status_code, response.text)

        try:
            body = response.json()
        except ValueError as exc:
            raise BadResponseShape(f"response was not JSON: {exc}") from exc

        text = self._extract(body)
        if not text or not text.strip():
            raise EmptyResponse("provider returned no text")
        return text


class OpenAICompatibleProvider(Provider):
    """Shared implementation for the OpenAI `/chat/completions` shape.

    Hugging Face, OpenRouter and NVIDIA NIM differ only in URL, credential and
    model namespace, so they get one implementation and three thin subclasses.
    """

    def _payload(self, model: str, system_prompt: str, user_prompt: str,
                 json_mode: bool, extras: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Generous on purpose: reasoning models spend most of this budget on
            # hidden reasoning tokens before emitting a single character.
            "max_tokens": self.settings.max_output_tokens,
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _extract(self, body: dict[str, Any]) -> str:
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BadResponseShape(f"no choices[0].message: {exc}") from exc

        content = message.get("content")
        if content and content.strip():
            return content

        # `reasoning_content` is chain of thought, never the answer. If content
        # is empty but reasoning is present, the model ran out of budget mid
        # thought - report it as empty so the router fails over.
        if (message.get("reasoning_content") or "").strip():
            raise EmptyResponse("content empty; budget consumed by reasoning tokens")
        raise EmptyResponse("content was null or blank")
