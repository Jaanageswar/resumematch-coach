"""Password gate and request limits for a public deployment. No network."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from backend import security
from backend.providers.router import ProviderRouter
from backend.security import Limiter, SecurityConfig

CHAT = {"messages": [{"role": "user", "content": "hello"}]}


def basic(password: str, user: str = "demo") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client(memdb, monkeypatch):
    from backend import main
    monkeypatch.setattr(main, "router",
                        ProviderRouter(main.settings, providers=[], log_attempts=False))
    monkeypatch.setattr(security, "LIMITER", Limiter())
    return TestClient(main.app)


def configure(monkeypatch, **kw):
    monkeypatch.setattr(security, "CONFIG", SecurityConfig(**kw))


# --------------------------------------------------------------------------- #
# local default: everything open, nothing limited
# --------------------------------------------------------------------------- #
def test_local_default_is_fully_open(client, monkeypatch):
    configure(monkeypatch)
    assert client.get("/").status_code == 200
    for _ in range(5):
        assert client.post("/chat", json=CHAT).status_code == 200


# --------------------------------------------------------------------------- #
# password gate
# --------------------------------------------------------------------------- #
def test_password_required_when_set(client, monkeypatch):
    configure(monkeypatch, password="s3cret")
    res = client.get("/")
    assert res.status_code == 401
    # The header is what makes the browser show its login prompt.
    assert res.headers["www-authenticate"].startswith("Basic ")


def test_correct_password_with_any_username(client, monkeypatch):
    configure(monkeypatch, password="s3cret")
    assert client.get("/", headers=basic("s3cret")).status_code == 200
    assert client.get("/", headers=basic("s3cret", user="anyone")).status_code == 200


@pytest.mark.parametrize("headers", [
    basic("wrong"),
    basic(""),
    {"Authorization": "Basic !!!not-base64!!!"},
    {"Authorization": "Bearer s3cret"},
    {"Authorization": "Basic " + base64.b64encode(b"no-colon").decode()},
])
def test_bad_credentials_are_401_never_500(client, monkeypatch, headers):
    configure(monkeypatch, password="s3cret")
    assert client.get("/history", headers=headers).status_code == 401


def test_api_routes_are_gated_too(client, monkeypatch):
    configure(monkeypatch, password="s3cret")
    assert client.post("/chat", json=CHAT).status_code == 401
    assert client.get("/providers/status").status_code == 401
    assert client.post("/chat", json=CHAT, headers=basic("s3cret")).status_code == 200


def test_health_stays_open_for_the_platform_health_check(client, monkeypatch):
    configure(monkeypatch, password="s3cret")
    assert client.get("/health").status_code == 200


# --------------------------------------------------------------------------- #
# fail closed
# --------------------------------------------------------------------------- #
def test_render_without_a_password_locks_itself(client, monkeypatch):
    """An accidentally open public deployment is the expensive mistake."""
    configure(monkeypatch, on_render=True, password="")
    res = client.get("/")
    assert res.status_code == 503
    assert "APP_PASSWORD" in res.json()["detail"]
    assert client.post("/chat", json=CHAT).status_code == 503
    assert client.get("/health").status_code == 200       # Render can still see it


def test_render_with_a_password_works(client, monkeypatch):
    configure(monkeypatch, on_render=True, password="s3cret")
    assert client.get("/", headers=basic("s3cret")).status_code == 200


# --------------------------------------------------------------------------- #
# limits
# --------------------------------------------------------------------------- #
def test_hourly_limit_returns_a_readable_429(client, monkeypatch):
    configure(monkeypatch, rate_per_hour=2)
    assert client.post("/chat", json=CHAT).status_code == 200
    assert client.post("/chat", json=CHAT).status_code == 200
    res = client.post("/chat", json=CHAT)
    assert res.status_code == 429
    assert "2 AI requests per hour" in res.json()["detail"]
    assert res.headers["retry-after"] == "3600"


def test_cheap_routes_do_not_count_against_the_limit(client, monkeypatch):
    configure(monkeypatch, rate_per_hour=1)
    for _ in range(5):
        assert client.get("/history").status_code == 200
        assert client.get("/sample").status_code == 200
    assert client.post("/chat", json=CHAT).status_code == 200


def test_limit_is_per_ip():
    cfg = SecurityConfig(rate_per_hour=1)
    lim = Limiter()
    assert lim.check("1.1.1.1", cfg, now=1000) is None
    assert lim.check("1.1.1.1", cfg, now=1001) is not None
    assert lim.check("2.2.2.2", cfg, now=1002) is None     # someone else is fine


def test_hourly_window_rolls_forward():
    cfg = SecurityConfig(rate_per_hour=1)
    lim = Limiter()
    assert lim.check("ip", cfg, now=1000) is None
    assert lim.check("ip", cfg, now=2000) is not None
    assert lim.check("ip", cfg, now=1000 + 3601) is None    # an hour later


def test_daily_cap_applies_across_everyone_and_resets():
    cfg = SecurityConfig(daily_cap=2)
    lim = Limiter()
    day1 = 1_800_000_000                                     # a fixed UTC instant
    assert lim.check("a", cfg, now=day1) is None
    assert lim.check("b", cfg, now=day1 + 1) is None
    refusal = lim.check("c", cfg, now=day1 + 2)
    assert refusal and "daily" in refusal
    assert lim.check("c", cfg, now=day1 + 86400) is None     # next UTC day


def test_limits_off_means_unlimited():
    cfg = SecurityConfig()
    lim = Limiter()
    for i in range(1000):
        assert lim.check("ip", cfg, now=1000 + i) is None
