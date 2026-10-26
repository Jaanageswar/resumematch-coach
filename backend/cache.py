"""Content-hash cache.

An identical (resume, job description) pair returns the stored analysis instantly
and spends zero API quota - which matters a lot on free tiers.

The hash is also the UNIQUE key on `analyses`, so the cache and the dedupe rule
are the same mechanism rather than two things that can disagree.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from . import db

# Bump this when a prompt or the output schema changes, so stale results from an
# older pipeline are not served as if they came from the current one.
CACHE_VERSION = "v1"

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Collapse whitespace and case so trivial edits still hit the cache.

    Re-pasting the same resume with different line wrapping or trailing spaces
    should not cost another API call.
    """
    return _WHITESPACE.sub(" ", (text or "").strip()).lower()


def input_hash(resume_text: str, job_text: str, owner: str | None = None) -> str:
    """Cache key for one (resume, job) pair - per visitor when the app is public.

    The key is UNIQUE in the analyses table. Without the owner in it, two
    strangers analysing the same texts would share one row, and the second
    visitor's save would take the row over - deleting it from the first
    visitor's history. Locally owner is None and the key is unchanged.
    """
    joined = f"{normalise(resume_text)}||{normalise(job_text)}||{CACHE_VERSION}"
    if owner:
        joined += f"||{owner}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get_cached(
    resume_text: str,
    job_text: str,
    conn: sqlite3.Connection | None = None,
    owner: str | None = None,
) -> dict[str, Any] | None:
    """Return the stored analysis for these inputs, or None."""
    cached = db.get_analysis_by_hash(input_hash(resume_text, job_text, owner), conn=conn)
    if cached is None:
        return None
    return {**cached, "cached": True}
