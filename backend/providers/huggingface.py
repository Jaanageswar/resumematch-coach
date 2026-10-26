"""Hugging Face Inference Providers.

HF does not host these models itself - it routes the request to a partner
(Groq, Cerebras, Together, Fireworks, Novita, ...) and bills your free credits.
The model ID in the request body selects the route, so the URL never changes.

The token must be a FINE-GRAINED token with the "Make calls to Inference
Providers" permission (`inference.serverless.write`). A repo-scoped token is
valid for the Hub but returns 403 here - see HUGGINGFACE.md.

Of the four providers this is the only one documenting `json_schema` structured
output, so it is the best home for strict-JSON agents once its token works.
"""

from __future__ import annotations

from .base import OpenAICompatibleProvider


class HuggingFaceProvider(OpenAICompatibleProvider):
    name = "huggingface"
    url = "https://router.huggingface.co/v1/chat/completions"
    supports_json_mode = True
