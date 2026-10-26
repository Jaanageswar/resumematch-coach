#!/usr/bin/env python3
"""Probe every configured LLM provider and report what actually works.

Why this exists: free-tier model IDs rotate constantly (OpenRouter's ":free"
slugs change weekly, NVIDIA deprecates catalog entries, Cohere renames models).
A stale ID surfaces as an opaque 404 at demo time. This script turns that into a
10-second diagnosis.

Deliberately uses only the standard library, so it runs before `pip install`
and keeps working even if the project's dependencies break.

It NEVER prints an API key - only whether one is present.

Usage:
    python scripts/check_providers.py              # probe configured providers
    python scripts/check_providers.py --discover   # also list free/available models
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 30

# A trivial prompt - cheapest possible call that still proves the path works.
PROBE_SYSTEM = "You are a terse assistant."
PROBE_USER = "Reply with exactly one word: OK"

# Reasoning models burn most of their budget on hidden reasoning tokens BEFORE
# emitting any content. Verified 2026-10-23: nemotron-3.5-lightning returned
# HTTP 200 with content=null at max_tokens=600 (976 reasoning tokens used), and
# correct JSON at 2000. A probe must therefore be generous, or it reports a
# false failure for a perfectly good model.
PROBE_MAX_TOKENS = 1500

# Candidate model IDs tried in order. The first that answers wins and is
# reported as the value to put in .env.
# Models marked VERIFIED answered correctly on 2026-10-23. Note that NVIDIA's
# /v1/models lists 84 entries but most return "Not found for account" on the
# free Developer tier - the catalog is not an entitlement list.
CANDIDATES: dict[str, list[str]] = {
    "huggingface": [
        "openai/gpt-oss-120b",
        "Qwen/Qwen2.5-7B-Instruct",
        "meta-llama/Llama-3.1-8B-Instruct",
        "google/gemma-2-2b-it",
    ],
    "openrouter": [],  # filled from the live /models listing at runtime
    "nvidia": [
        "minimaxai/minimax-m3.1-flash",             # VERIFIED ~7s
        "nvidia/nemotron-3.5-lightning-30b-a3b",    # VERIFIED ~11s (reasoning)
        "nvidia/nemotron-3-ultra-550b-a55b",        # VERIFIED
        "deepseek-ai/deepseek-v4.1-flash",          # listed; timed out when probed
    ],
    "cohere": [
        "command-a-plus-05-2026",                   # VERIFIED ~3s, 436k ctx
        "command-a-03-2025",
        "command-r-08-2024",                        # VERIFIED
        "command-r7b-12-2024",
    ],
}


# --------------------------------------------------------------------------- #
# env loading (no python-dotenv dependency)
# --------------------------------------------------------------------------- #
def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class HttpResult:
    def __init__(self, status: int, body: str, latency_ms: int):
        self.status = status
        self.body = body
        self.latency_ms = latency_ms

    def json(self):
        return json.loads(self.body)


def request_json(
    url: str, token: str, payload: dict | None = None, method: str = "POST"
) -> HttpResult:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            return HttpResult(resp.status, body, int((time.monotonic() - t0) * 1000))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return HttpResult(e.code, body, int((time.monotonic() - t0) * 1000))
    except urllib.error.URLError as e:
        return HttpResult(0, f"network error: {e.reason}", int((time.monotonic() - t0) * 1000))
    except TimeoutError:
        return HttpResult(0, "timeout", int((time.monotonic() - t0) * 1000))


# --------------------------------------------------------------------------- #
# provider definitions
# --------------------------------------------------------------------------- #
def openai_payload(model: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": PROBE_SYSTEM},
            {"role": "user", "content": PROBE_USER},
        ],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
    }


def openai_extract(body: dict) -> str:
    msg = body["choices"][0]["message"]
    content = msg.get("content")
    if not content or not content.strip():
        # Reasoning models put their chain of thought in `reasoning_content` and
        # can legitimately return content=null if the token budget ran out first.
        # Treat it as an empty response - the router must fail over, not crash.
        reasoning = (msg.get("reasoning_content") or "").strip()
        hint = " (budget consumed by reasoning tokens)" if reasoning else ""
        raise ValueError(f"empty content{hint}")
    return content


def cohere_payload(model: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": PROBE_SYSTEM},
            {"role": "user", "content": PROBE_USER},
        ],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
    }


def cohere_extract(body: dict) -> str:
    """Cohere v2: message.content is a LIST of typed blocks.

    Verified 2026-10-23: command-a-plus-05-2026 returns
    content = [{"type": "thinking", ...}, {"type": "text", "text": "..."}]
    so indexing content[0] grabs the chain of thought, not the answer.
    Always filter by type == "text".
    """
    blocks = body["message"]["content"]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    if not text.strip():
        raise ValueError(f"no text block (got types: {[b.get('type') for b in blocks]})")
    return text


PROVIDERS = [
    {
        "name": "huggingface",
        "url": "https://router.huggingface.co/v1/chat/completions",
        "key_env": "HF_TOKEN",
        "model_env": "HF_MODEL",
        "payload": openai_payload,
        "extract": openai_extract,
        "models_url": "https://router.huggingface.co/v1/models",
    },
    {
        "name": "openrouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "payload": openai_payload,
        "extract": openai_extract,
        "models_url": "https://openrouter.ai/api/v1/models",
    },
    {
        "name": "nvidia",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_env": "NVIDIA_API_KEY",
        "model_env": "NVIDIA_MODEL",
        "payload": openai_payload,
        "extract": openai_extract,
        "models_url": "https://integrate.api.nvidia.com/v1/models",
    },
    {
        "name": "cohere",
        "url": "https://api.cohere.com/v2/chat",
        "key_env": "COHERE_API_KEY",
        "model_env": "COHERE_MODEL",
        "payload": cohere_payload,
        "extract": cohere_extract,
        "models_url": "https://api.cohere.com/v1/models",
    },
]


def short_error(res: HttpResult) -> str:
    """Readable one-line reason. Truncated so a key can never leak via an echo."""
    try:
        body = res.json()
        for key in ("error", "message", "detail"):
            val = body.get(key)
            if isinstance(val, dict):
                val = val.get("message") or json.dumps(val)
            if val:
                return str(val)[:120]
        return json.dumps(body)[:120]
    except Exception:
        return res.body.strip()[:120] or f"HTTP {res.status}"


def discover_openrouter_free(token: str) -> list[str]:
    res = request_json("https://openrouter.ai/api/v1/models", token, method="GET")
    if res.status != 200:
        return []
    try:
        models = res.json().get("data", [])
    except Exception:
        return []
    free = [m["id"] for m in models if str(m.get("id", "")).endswith(":free")]
    # Prefer general-purpose instruct models over tiny or coder-specific ones.
    free.sort(key=lambda mid: ("coder" in mid, "vl" in mid, len(mid)))
    return free


def probe(provider: dict, token: str, models: list[str]) -> dict:
    """Try each candidate model; return the first success, else the last error."""
    last = {"ok": False, "model": models[0] if models else "-", "error": "no models to try",
            "latency_ms": 0}
    for model in models:
        res = request_json(provider["url"], token, provider["payload"](model))
        if res.status == 200:
            try:
                text = provider["extract"](res.json()).strip()
                return {"ok": True, "model": model, "reply": text[:40],
                        "latency_ms": res.latency_ms, "error": ""}
            except Exception as e:
                last = {"ok": False, "model": model,
                        "error": f"unparseable response shape: {e}",
                        "latency_ms": res.latency_ms}
                continue
        last = {"ok": False, "model": model, "error": f"HTTP {res.status}: {short_error(res)}",
                "latency_ms": res.latency_ms}
        # Auth failures will fail identically for every model - stop early.
        if res.status in (401, 403):
            break
    return last


def main() -> int:
    discover = "--discover" in sys.argv
    env = load_env(ROOT / ".env")
    if not env:
        print("No .env found. Copy .env.example to .env and add your keys.")
        return 1

    print()
    print("  Provider probe - sends one ~10-token request per provider")
    print("  " + "-" * 74)

    # Probe in the same order the app will try them, so the output mirrors runtime.
    order = [p.strip() for p in env.get("PROVIDER_ORDER", "").split(",") if p.strip()]
    by_name = {p["name"]: p for p in PROVIDERS}
    ordered = [by_name[n] for n in order if n in by_name]
    ordered += [p for p in PROVIDERS if p["name"] not in order]

    results = []
    for provider in ordered:
        name = provider["name"]
        token = env.get(provider["key_env"], "").strip()

        if not token:
            print(f"  {name:<13} no key - skipped")
            results.append((name, None))
            continue

        # Try the configured model first, then its declared fallbacks, then
        # the built-in candidate list.
        configured = env.get(provider["model_env"], "").strip()
        models = [configured] if configured else []
        fallback_env = provider["model_env"].replace("_MODEL", "_FALLBACK_MODELS")
        models += [m.strip() for m in env.get(fallback_env, "").split(",")
                   if m.strip() and m.strip() not in models]

        if name == "openrouter":
            free = discover_openrouter_free(token)
            if discover:
                print(f"  {name:<13} {len(free)} free models found"
                      + (f": {', '.join(free[:6])}" if free else ""))
            models += [m for m in free[:5] if m not in models]
        models += [m for m in CANDIDATES[name] if m not in models]

        out = probe(provider, token, models)
        if out["ok"]:
            print(f"  {name:<13} PASS  {out['model']:<42} {out['latency_ms']:>6} ms")
        else:
            print(f"  {name:<13} FAIL  {out['model']:<42} {out['latency_ms']:>6} ms")
            print(f"  {'':<13}       -> {out['error']}")
        results.append((name, out))

    print("  " + "-" * 74)
    working = [(n, r) for n, r in results if r and r["ok"]]
    print(f"  {len(working)} of {len(PROVIDERS)} providers working.")
    if working:
        print()
        print("  Put these in .env:")
        env_names = {p["name"]: p["model_env"] for p in PROVIDERS}
        for name, r in working:
            print(f"    {env_names[name]}={r['model']}")
    else:
        print("  No providers reachable - the app will use its local keyword fallback.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
