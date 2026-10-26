"""NVIDIA NIM (build.nvidia.com).

Two things learned by probing this with a real key (DESIGN.md s3):

1. `GET /v1/models` lists ~84 models, but most answer
   "Function '...': Not found for account ..." on the free Developer tier, and
   some are hard-410 end-of-life. A catalog listing is not an entitlement.
2. Several of the models that DO work are reasoning models. They return HTTP 200
   with `content: null` if `max_tokens` is too small, having spent the whole
   budget on `reasoning_content`. The base class turns that into EmptyResponse.
"""

from __future__ import annotations

from .base import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    name = "nvidia"
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    supports_json_mode = True
