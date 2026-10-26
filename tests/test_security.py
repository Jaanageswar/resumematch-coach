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


# --------------------------------------------------------------------------- #
# public mode
# --------------------------------------------------------------------------- #
def test_public_demo_opens_render_without_a_password(client, monkeypatch):
    configure(monkeypatch, on_render=True, public=True)
    assert client.get("/").status_code == 200
    assert client.post("/chat", json=CHAT).status_code == 200


def test_public_demo_still_applies_limits(client, monkeypatch):
    configure(monkeypatch, on_render=True, public=True, rate_per_hour=1)
    assert client.post("/chat", json=CHAT).status_code == 200
    assert client.post("/chat", json=CHAT).status_code == 429


def test_a_set_password_wins_over_public_mode(client, monkeypatch):
    """Both set is ambiguous; the safer reading is that the password applies."""
    configure(monkeypatch, on_render=True, public=True, password="s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/", headers=basic("s3cret")).status_code == 200


def test_locked_message_explains_both_ways_to_unlock(client, monkeypatch):
    configure(monkeypatch, on_render=True)
    detail = client.get("/").json()["detail"]
    assert "APP_PASSWORD" in detail and "PUBLIC_DEMO" in detail


# --------------------------------------------------------------------------- #
# per-visitor isolation
# --------------------------------------------------------------------------- #
from pathlib import Path                                   # noqa: E402

_SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
RESUME = (_SAMPLES / "resume.txt").read_text(encoding="utf-8")
JOB = (_SAMPLES / "job_description.txt").read_text(encoding="utf-8")


def two_visitors(client):
    """Same app, two separate cookie jars - two different people."""
    from backend import main
    return client, TestClient(main.app)


def test_visitors_cannot_see_each_others_analyses(client, monkeypatch):
    configure(monkeypatch, public=True)
    alice, bob = two_visitors(client)
    alice.get("/")                                        # each gets a cookie
    bob.get("/")

    made = alice.post("/analyze", json={"resume_text": RESUME, "job_text": JOB,
                                        "force": True}).json()
    aid = made["id"]

    assert [r["id"] for r in alice.get("/history").json()] == [aid]
    assert bob.get("/history").json() == []

    # Every way of reaching Alice's data by id is a plain 404 for Bob.
    assert bob.get(f"/analysis/{aid}").status_code == 404
    assert bob.get(f"/analysis/{aid}/resume.pdf").status_code == 404
    assert bob.post("/chat", json={**CHAT, "analysis_id": aid}).status_code == 404
    assert bob.post("/jobs", json={"analysis_id": aid}).status_code == 404

    bob_made = bob.post("/analyze", json={"resume_text": RESUME + "\nBob.",
                                          "job_text": JOB}).json()
    assert bob.get(f"/compare?a={bob_made['id']}&b={aid}").status_code == 404

    # Bob cannot delete it either, and it survives his attempt.
    assert bob.delete(f"/analysis/{aid}").status_code == 404
    assert alice.get(f"/analysis/{aid}").status_code == 200


def test_identical_input_from_two_visitors_does_not_take_over_a_row(client, monkeypatch):
    """The cache key is UNIQUE; without the visitor in it, Bob's save would
    steal Alice's row and it would vanish from her history."""
    configure(monkeypatch, public=True)
    alice, bob = two_visitors(client)
    alice.get("/"); bob.get("/")
    body = {"resume_text": RESUME, "job_text": JOB, "force": True}

    a = alice.post("/analyze", json=body).json()
    b = bob.post("/analyze", json=body).json()

    assert a["id"] != b["id"]
    assert [r["id"] for r in alice.get("/history").json()] == [a["id"]]
    assert [r["id"] for r in bob.get("/history").json()] == [b["id"]]


def test_isolation_cookie_is_httponly_and_lax(client, monkeypatch):
    configure(monkeypatch, public=True)
    header = client.get("/").headers.get("set-cookie", "")
    assert header.startswith(f"{security.OWNER_COOKIE}=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header


def test_a_forged_cookie_value_is_replaced_not_trusted(client, monkeypatch):
    configure(monkeypatch, public=True)
    client.cookies.set(security.OWNER_COOKIE, "x'; DROP TABLE analyses;--")
    header = client.get("/").headers.get("set-cookie", "")
    assert header.startswith(f"{security.OWNER_COOKIE}=")       # a fresh one issued
    assert "DROP" not in header


def test_local_use_stays_shared_and_sets_no_cookie(client, monkeypatch):
    """One person on their own machine expects to see everything."""
    configure(monkeypatch)
    assert "set-cookie" not in client.get("/").headers
    alice, bob = two_visitors(client)
    alice.post("/analyze", json={"resume_text": RESUME, "job_text": JOB, "force": True})
    assert len(bob.get("/history").json()) == 1


def test_old_databases_are_migrated_to_have_an_owner_column():
    from backend import db
    conn = db.connect(":memory:")
    # the analyses table exactly as it was before isolation existed
    conn.execute("""CREATE TABLE analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, resume_id INTEGER NOT NULL,
        job_id INTEGER NOT NULL, input_hash TEXT NOT NULL UNIQUE,
        score INTEGER NOT NULL, score_breakdown_json TEXT NOT NULL,
        matched_skills_json TEXT NOT NULL, missing_skills_json TEXT NOT NULL,
        rewritten_bullets_json TEXT NOT NULL, suggestions_json TEXT NOT NULL,
        summary TEXT, provider_used TEXT, model_used TEXT,
        source TEXT NOT NULL DEFAULT 'ai', partial INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    db.init_db(conn)
    db.init_db(conn)                                       # and idempotent
    cols = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
    assert "owner" in cols
    conn.close()
