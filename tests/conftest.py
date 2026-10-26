"""Shared fixtures. No test in this suite makes a network call."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import db                                  # noqa: E402
from backend.config import ProviderConfig, load_settings  # noqa: E402
from backend.providers.base import Provider             # noqa: E402


@pytest.fixture
def memdb():
    """A fresh in-memory database, installed as the shared connection."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.set_connection(conn)
    yield conn
    db.set_connection(None)
    conn.close()


@pytest.fixture
def settings():
    return load_settings()


class FakeProvider(Provider):
    """A provider that answers from a script instead of the network.

    `responses` is a list consumed one per call; an Exception instance in the
    list is raised instead of returned, which is how failure sequences are
    expressed in tests.
    """

    def __init__(self, name, settings, *, responses=None, models=("m1",), key="k"):
        config = ProviderConfig(
            name=name, api_key=key, model=models[0],
            fallback_models=tuple(models[1:]),
        )
        super().__init__(config, settings)
        self.name = name
        self.responses = list(responses or ['{"ok": true}'])
        self.calls: list[str] = []

    def _payload(self, model, system_prompt, user_prompt, json_mode, extras=True):
        return {}

    def _extract(self, body):
        return ""

    def generate(self, system_prompt, user_prompt, *, model=None, json_mode=True):
        self.calls.append(model or self.models[0])
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake():
    return FakeProvider
