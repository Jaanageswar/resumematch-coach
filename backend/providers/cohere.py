"""Cohere v2 Chat.

The one provider that is NOT OpenAI-compatible, and the reason the whole
provider layer is built around an interface rather than a shared helper.

Response shape (verified 2026-10-23 against command-a-plus-05-2026):

    {"message": {"content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "text",     "text": "{...the actual answer...}"}
    ]}}

`content` is a LIST OF TYPED BLOCKS, and `content[0]` is the model's chain of
thought, not its answer. The docs' hello-world example shows `content[0].text`
because simple models emit a single text block - follow that example with a
thinking-capable model and you silently parse the reasoning instead of the
result. Always filter on type == "text".
"""

from __future__ import annotations

from typing import Any

from .base import BadResponseShape, EmptyResponse, Provider


class CohereProvider(Provider):
    name = "cohere"
    url = "https://api.cohere.com/v2/chat"
    supports_json_mode = True

    def _payload(self, model: str, system_prompt: str, user_prompt: str,
                 json_mode: bool, extras: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.settings.max_output_tokens,
            "temperature": 0.2,
        }
        if json_mode:
            # Cohere rejects response_format alongside tools or documents; we
            # send neither, so plain JSON mode is safe here.
            payload["response_format"] = {"type": "json_object"}
        if extras:
            # Measured on a full resume + job description (2026-10-23):
            #   thinking on  -> 20.5s, and at max_tokens=2000 it burned the
            #                   entire budget on reasoning and returned a
            #                   'thinking' block with NO 'text' block at all.
            #   thinking off ->  3.5s, blocks=['text'], valid JSON every time.
            # We want a structured extraction, not deliberation, so it is off.
            # `extras=False` drops this for any model that rejects the field.
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _extract(self, body: dict[str, Any]) -> str:
        try:
            blocks = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise BadResponseShape(f"no message.content: {exc}") from exc

        if not isinstance(blocks, list):
            raise BadResponseShape(f"message.content was {type(blocks).__name__}, not a list")

        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            types = [b.get("type") for b in blocks if isinstance(b, dict)]
            raise EmptyResponse(f"no text block (block types: {types})")
        return text
