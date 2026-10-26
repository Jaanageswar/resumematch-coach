"""Protection for a public deployment: a password gate and request limits.

Locally none of this is active - every limit defaults to off and no password
is required, so `run.ps1` behaves exactly as before.

Deployed (e.g. on Render), the app is reachable by anyone with the URL, and
every AI request spends the owner's free provider quota. So:

- APP_PASSWORD   gates every route behind HTTP Basic auth. The browser shows
                 its own login prompt once and then sends the credentials with
                 every request, including the app's fetch() calls, so the
                 frontend needs no changes. /health stays open because Render's
                 health checker cannot log in.
- Fail closed    on Render with no APP_PASSWORD set, the app refuses to serve
                 rather than running wide open. An accidentally public app is
                 the expensive mistake; a locked one is a one-line fix.
- RATE_LIMIT_PER_HOUR  AI requests allowed per visitor IP per rolling hour.
- DAILY_AI_CAP         AI requests allowed across all visitors per UTC day.

Limits are in memory, so they reset when the process restarts. That is fine
for a free-tier demo; it is not a substitute for a real quota service.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

# Only these spend AI quota. /extract, /history and friends are cheap.
AI_PATHS = ("/analyze", "/chat", "/jobs")
OPEN_PATHS = ("/health",)
REALM = "ResumeMatch Coach demo"


def _int_env(name: str) -> int:
    try:
        return max(0, int(os.getenv(name, "0").strip() or 0))
    except ValueError:
        return 0


@dataclass
class SecurityConfig:
    password: str = ""
    on_render: bool = False
    rate_per_hour: int = 0        # 0 = unlimited
    daily_cap: int = 0            # 0 = unlimited


def load() -> SecurityConfig:
    return SecurityConfig(
        password=os.getenv("APP_PASSWORD", "").strip(),
        on_render=os.getenv("RENDER", "").strip().lower() == "true",
        rate_per_hour=_int_env("RATE_LIMIT_PER_HOUR"),
        daily_cap=_int_env("DAILY_AI_CAP"),
    )


@dataclass
class Limiter:
    """Rolling one-hour window per IP, plus a single counter per UTC day."""

    per_ip: dict[str, deque] = field(default_factory=dict)
    day: str = ""
    day_count: int = 0

    def check(self, ip: str, cfg: SecurityConfig, now: float | None = None) -> str | None:
        """Record one AI request. Returns a refusal message, or None if allowed."""
        now = time.time() if now is None else now

        today = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.day_count = today, 0
        if cfg.daily_cap and self.day_count >= cfg.daily_cap:
            return ("This demo has reached its daily AI limit, which protects the "
                    "owner's free API quota. It resets at midnight UTC.")

        if cfg.rate_per_hour:
            window = self.per_ip.setdefault(ip, deque())
            while window and now - window[0] > 3600:
                window.popleft()
            if len(window) >= cfg.rate_per_hour:
                wait = max(1, int((3600 - (now - window[0])) // 60) + 1)
                return (f"Demo limit reached: {cfg.rate_per_hour} AI requests per hour. "
                        f"Try again in about {wait} minute{'s' if wait != 1 else ''}.")
            window.append(now)
            # Keep the table from growing forever on a long-running process.
            if len(self.per_ip) > 5000:
                self.per_ip = {k: v for k, v in self.per_ip.items() if v}

        self.day_count += 1
        return None


CONFIG = load()
LIMITER = Limiter()


def _password_ok(header: str | None, expected: str) -> bool:
    """Check an HTTP Basic header. Any username is accepted; the password is
    the secret, compared in constant time."""
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    _, sep, supplied = decoded.partition(":")
    return bool(sep) and secrets.compare_digest(supplied.encode(), expected.encode())


async def gate(request: Request, call_next):
    """HTTP middleware: password gate first, then AI request limits.

    Reads the module-level CONFIG on every request, so tests (and a future
    settings reload) can swap it without rebuilding the app.
    """
    cfg = CONFIG
    path = request.url.path

    if path in OPEN_PATHS:
        return await call_next(request)

    if cfg.on_render and not cfg.password:
        return JSONResponse(
            status_code=503,
            content={"detail": "This deployment is locked because APP_PASSWORD is not "
                               "set. Add it in the Render dashboard to unlock it."},
        )

    if cfg.password and not _password_ok(request.headers.get("authorization"), cfg.password):
        return JSONResponse(
            status_code=401,
            content={"detail": "Password required."},
            headers={"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'},
        )

    if request.method == "POST" and path in AI_PATHS:
        # Behind Render's proxy, uvicorn already rewrites client.host to the
        # real visitor IP (Render sets FORWARDED_ALLOW_IPS=*).
        ip = request.client.host if request.client else "unknown"
        refusal = LIMITER.check(ip, cfg)
        if refusal:
            return JSONResponse(status_code=429, content={"detail": refusal},
                                headers={"Retry-After": "3600"})

    return await call_next(request)
