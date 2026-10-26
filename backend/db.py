"""SQLite access layer. Parameterised queries only - no f-string SQL anywhere.

Design notes:
- One shared connection guarded by an RLock. FastAPI serves requests on a thread
  pool, so `check_same_thread=False` plus explicit locking is simpler and safer
  here than a connection pool, and SQLite serialises writes regardless.
- `PRAGMA foreign_keys = ON` must be set per connection; SQLite silently ignores
  foreign keys otherwise.
- JSON is stored in TEXT columns (SQLite has no JSON type) and decoded at the
  edge by `row_to_analysis`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_descriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resume_id INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
  job_id    INTEGER NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
  input_hash TEXT NOT NULL UNIQUE,
  score INTEGER NOT NULL,
  score_breakdown_json   TEXT NOT NULL,
  matched_skills_json    TEXT NOT NULL,
  missing_skills_json    TEXT NOT NULL,
  rewritten_bullets_json TEXT NOT NULL,
  suggestions_json       TEXT NOT NULL,
  summary TEXT,
  provider_used TEXT,
  model_used TEXT,
  source TEXT NOT NULL DEFAULT 'ai',
  partial INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
  provider TEXT NOT NULL,
  model TEXT,
  success INTEGER NOT NULL,
  latency_ms INTEGER,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Job suggestions depend only on the resume, so they are cached by a hash of
-- it. Re-opening an analysis, or asking Pip again, costs no API quota.
CREATE TABLE IF NOT EXISTS job_suggestions (
  input_hash TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analyses_hash    ON analyses(input_hash);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_job     ON analyses(job_id);
CREATE INDEX IF NOT EXISTS idx_logs_created     ON provider_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_provider    ON provider_logs(provider, created_at DESC);
"""


def get_conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = connect(settings.db_path)
        return _CONN


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with the pragmas and row factory this app expects.

    Exposed separately so tests can pass ':memory:' without touching the real
    database file.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Create tables and indexes. Idempotent - safe to call on every startup."""
    target = conn or get_conn()
    with _LOCK:
        target.executescript(SCHEMA)
        target.commit()


def set_connection(conn: sqlite3.Connection | None) -> None:
    """Replace the shared connection. Used by tests only."""
    global _CONN
    with _LOCK:
        _CONN = conn


@contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    target = conn or get_conn()
    with _LOCK:
        try:
            yield target
            target.commit()
        except Exception:
            target.rollback()
            raise


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
def _get_or_insert(
    table: str, name_col: str, name: str, content: str,
    conn: sqlite3.Connection | None,
) -> int:
    """Reuse the existing row when the content is identical.

    Inserting a new row for every analysis looked harmless but broke the score
    trend: analysing two resume versions against the same job created two job
    rows, so "score over time for this job" only ever found one point.
    Deduplicating on content is what makes the trend meaningful.
    """
    with transaction(conn) as c:
        row = c.execute(
            f"SELECT id FROM {table} WHERE content = ? ORDER BY id LIMIT 1",
            (content,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = c.execute(
            f"INSERT INTO {table} ({name_col}, content) VALUES (?, ?)",
            (name, content),
        )
        return int(cur.lastrowid)


def insert_resume(label: str, content: str, conn: sqlite3.Connection | None = None) -> int:
    return _get_or_insert("resumes", "label", label, content, conn)


def insert_job(title: str, content: str, conn: sqlite3.Connection | None = None) -> int:
    return _get_or_insert("job_descriptions", "title", title, content, conn)


def upsert_analysis(
    *,
    resume_id: int,
    job_id: int,
    input_hash: str,
    score: int,
    score_breakdown: dict[str, int],
    matched_skills: list[str],
    missing_skills: list[str],
    rewritten_bullets: list[dict[str, str]],
    suggestions: list[str],
    summary: str,
    provider_used: str | None,
    model_used: str | None,
    source: str,
    partial: bool,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Insert, or overwrite the row with this input_hash (a forced re-analyze).

    The UNIQUE constraint on input_hash makes the cache and the dedupe rule the
    same mechanism, so there is no second table to drift out of sync.
    """
    payload = (
        resume_id,
        job_id,
        input_hash,
        int(score),
        json.dumps(score_breakdown),
        json.dumps(matched_skills),
        json.dumps(missing_skills),
        json.dumps(rewritten_bullets),
        json.dumps(suggestions),
        summary,
        provider_used,
        model_used,
        source,
        1 if partial else 0,
    )
    with transaction(conn) as c:
        cur = c.execute(
            """
            INSERT INTO analyses (
              resume_id, job_id, input_hash, score, score_breakdown_json,
              matched_skills_json, missing_skills_json, rewritten_bullets_json,
              suggestions_json, summary, provider_used, model_used, source, partial
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(input_hash) DO UPDATE SET
              resume_id = excluded.resume_id,
              job_id = excluded.job_id,
              score = excluded.score,
              score_breakdown_json = excluded.score_breakdown_json,
              matched_skills_json = excluded.matched_skills_json,
              missing_skills_json = excluded.missing_skills_json,
              rewritten_bullets_json = excluded.rewritten_bullets_json,
              suggestions_json = excluded.suggestions_json,
              summary = excluded.summary,
              provider_used = excluded.provider_used,
              model_used = excluded.model_used,
              source = excluded.source,
              partial = excluded.partial,
              created_at = datetime('now')
            RETURNING id
            """,
            payload,
        )
        # RETURNING, not cursor.lastrowid: on the ON CONFLICT ... DO UPDATE
        # path lastrowid holds the connection's last INSERT rowid, which is a
        # different (or nonexistent) analysis. Trusting it produced a bogus id
        # and a FOREIGN KEY failure when backfilling provider_logs.
        row = cur.fetchone()
        if row is None:  # pragma: no cover - defensive
            row = c.execute(
                "SELECT id FROM analyses WHERE input_hash = ?", (input_hash,)
            ).fetchone()
        return int(row["id"])


def log_provider_attempt(
    *,
    provider: str,
    model: str | None,
    success: bool,
    latency_ms: int | None,
    error: str | None = None,
    analysis_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Record one provider attempt. Never stores credentials - only outcomes."""
    with transaction(conn) as c:
        cur = c.execute(
            """
            INSERT INTO provider_logs
              (analysis_id, provider, model, success, latency_ms, error)
            VALUES (?,?,?,?,?,?)
            """,
            (analysis_id, provider, model, 1 if success else 0, latency_ms, error),
        )
        return int(cur.lastrowid)


def attach_logs_to_analysis(
    log_ids: list[int], analysis_id: int, conn: sqlite3.Connection | None = None
) -> None:
    """Link attempts logged before the analysis row existed.

    Attempts are logged as they happen, which is necessarily before the analysis
    has an id, so the foreign key is backfilled once the row is written.
    """
    if not log_ids:
        return
    placeholders = ",".join("?" for _ in log_ids)
    with transaction(conn) as c:
        c.execute(
            f"UPDATE provider_logs SET analysis_id = ? WHERE id IN ({placeholders})",
            (analysis_id, *log_ids),
        )


def delete_analysis(analysis_id: int, conn: sqlite3.Connection | None = None) -> bool:
    with transaction(conn) as c:
        cur = c.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def row_to_analysis(row: sqlite3.Row) -> dict[str, Any]:
    """Decode a stored row into the shape the API returns."""
    return {
        "id": row["id"],
        "score": row["score"],
        "score_breakdown": json.loads(row["score_breakdown_json"]),
        "matched_skills": json.loads(row["matched_skills_json"]),
        "missing_skills": json.loads(row["missing_skills_json"]),
        "rewritten_bullets": json.loads(row["rewritten_bullets_json"]),
        "suggestions": json.loads(row["suggestions_json"]),
        "summary": row["summary"] or "",
        "provider_used": row["provider_used"],
        "model_used": row["model_used"],
        "source": row["source"],
        "partial": bool(row["partial"]),
        "created_at": row["created_at"],
        "resume_label": row["resume_label"] if "resume_label" in row.keys() else None,
        "job_title": row["job_title"] if "job_title" in row.keys() else None,
    }


_ANALYSIS_SELECT = """
SELECT a.*, r.label AS resume_label, j.title AS job_title
FROM analyses a
JOIN resumes r ON r.id = a.resume_id
JOIN job_descriptions j ON j.id = a.job_id
"""


def get_analysis(analysis_id: int, conn: sqlite3.Connection | None = None):
    c = conn or get_conn()
    with _LOCK:
        row = c.execute(_ANALYSIS_SELECT + " WHERE a.id = ?", (analysis_id,)).fetchone()
    return row_to_analysis(row) if row else None


def get_analysis_by_hash(input_hash: str, conn: sqlite3.Connection | None = None):
    c = conn or get_conn()
    with _LOCK:
        row = c.execute(
            _ANALYSIS_SELECT + " WHERE a.input_hash = ?", (input_hash,)
        ).fetchone()
    return row_to_analysis(row) if row else None


def get_analysis_sources(
    analysis_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    """The original resume and job text behind one analysis.

    The analysis row stores the *result*; the PDF export needs the input it was
    computed from, so the rewrites can be applied back into it.
    """
    c = conn or get_conn()
    with _LOCK:
        row = c.execute(
            """
            SELECT r.label AS resume_label, r.content AS resume_text,
                   j.title AS job_title,    j.content AS job_text
            FROM analyses a
            JOIN resumes r ON r.id = a.resume_id
            JOIN job_descriptions j ON j.id = a.job_id
            WHERE a.id = ?
            """,
            (analysis_id,),
        ).fetchone()
    return dict(row) if row else None


def list_analyses(
    *,
    limit: int = 50,
    job_title: str | None = None,
    min_score: int | None = None,
    source: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if job_title:
        clauses.append("j.title LIKE ?")
        params.append(f"%{job_title}%")
    if min_score is not None:
        clauses.append("a.score >= ?")
        params.append(min_score)
    if source:
        clauses.append("a.source = ?")
        params.append(source)

    sql = _ANALYSIS_SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))

    c = conn or get_conn()
    with _LOCK:
        rows = c.execute(sql, tuple(params)).fetchall()
    return [row_to_analysis(r) for r in rows]


def score_history(job_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Score over time for one job - powers the Compare tab's trend chart."""
    c = conn or get_conn()
    with _LOCK:
        rows = c.execute(
            """
            SELECT a.id, a.score, a.created_at, r.label AS resume_label
            FROM analyses a JOIN resumes r ON r.id = a.resume_id
            WHERE a.job_id = ? ORDER BY a.created_at ASC
            """,
            (job_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_provider_logs(limit: int = 20, conn: sqlite3.Connection | None = None):
    c = conn or get_conn()
    with _LOCK:
        rows = c.execute(
            """
            SELECT id, analysis_id, provider, model, success, latency_ms, error, created_at
            FROM provider_logs ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [dict(r) for r in rows]


def provider_stats(conn: sqlite3.Connection | None = None) -> dict[str, dict[str, Any]]:
    """Per-provider attempt count, success rate and average latency.

    Latency is averaged over successful attempts only; a 200ms connection
    refusal would otherwise make a dead provider look fast.
    """
    c = conn or get_conn()
    with _LOCK:
        rows = c.execute(
            """
            SELECT provider,
                   COUNT(*) AS attempts,
                   SUM(success) AS successes,
                   AVG(CASE WHEN success = 1 THEN latency_ms END) AS avg_latency
            FROM provider_logs GROUP BY provider
            """
        ).fetchall()
    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        attempts = row["attempts"] or 0
        successes = row["successes"] or 0
        stats[row["provider"]] = {
            "attempts": attempts,
            "successes": successes,
            "success_rate": round(successes / attempts, 3) if attempts else None,
            "avg_latency_ms": int(row["avg_latency"]) if row["avg_latency"] else None,
        }
    return stats


# --------------------------------------------------------------------------- #
# job suggestion cache
# --------------------------------------------------------------------------- #
def get_job_suggestions(input_hash: str, conn: sqlite3.Connection | None = None):
    c = conn or get_conn()
    with _LOCK:
        row = c.execute(
            "SELECT payload_json FROM job_suggestions WHERE input_hash = ?",
            (input_hash,),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def save_job_suggestions(
    input_hash: str, payload: dict[str, Any], conn: sqlite3.Connection | None = None
) -> None:
    with transaction(conn) as c:
        c.execute(
            """
            INSERT INTO job_suggestions (input_hash, payload_json) VALUES (?, ?)
            ON CONFLICT(input_hash) DO UPDATE SET
              payload_json = excluded.payload_json, created_at = datetime('now')
            """,
            (input_hash, json.dumps(payload)),
        )
