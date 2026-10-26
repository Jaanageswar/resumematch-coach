"""Orchestrator: runs the pipeline, merges the results, persists, degrades.

    cache -> Analyst -> Rewriter -> merge -> validate -> save

Two independent degradation paths, which is what makes the promise "this cannot
return an error page" true:

- Rewriter fails  -> `partial=true`. The score and gap analysis still render;
                     only the rewritten bullets are missing.
- Analyst fails   -> `source="local_fallback"`. The deterministic keyword
                     analyzer answers instead. No AI, but a real result.

Provider-level failover happens a layer down in ProviderRouter. This module
only decides what to do once a whole agent has given up.
"""

from __future__ import annotations

import sqlite3
import time

from .. import cache, db
from ..config import Settings, settings as default_settings
from ..fallback import local_analyzer
from ..models import AnalysisResult, AnalystOutput, RewriterOutput
from ..providers.router import AllProvidersFailed, ProviderRouter
from .analyst import AnalystAgent
from .base import AgentError
from .rewriter import RewriterAgent

MIN_INPUT_CHARS = 30

# Below this, the "What to do next" card looks broken rather than brief.
MIN_SUGGESTIONS = 3


def _top_up_suggestions(existing: list[str], missing_skills: list[str]) -> list[str]:
    """Pad thin suggestion lists with gap-derived advice.

    The Rewriter prompt asks for 3-5 suggestions, and free-tier models
    sometimes return one - or none at all while still filling in the bullets.
    The padding is built from the skills the Analyst actually found missing, so
    it is true regardless of what the model did or did not say.
    """
    out = list(existing)
    seen = {item.lower() for item in out}
    for extra in local_analyzer.fallback_suggestions(missing_skills):
        if len(out) >= MIN_SUGGESTIONS:
            break
        if extra.lower() not in seen:
            seen.add(extra.lower())
            out.append(extra)
    return out[:6]


class InputTooShort(ValueError):
    """Not enough text to analyse anything meaningful."""


def _derive_job_title(job_text: str) -> str:
    """First non-empty line of the posting, which is almost always the title."""
    for line in job_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "Untitled role"


def _derive_resume_label(resume_text: str) -> str:
    for line in resume_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "Untitled resume"


def analyze(
    resume_text: str,
    job_text: str,
    *,
    resume_label: str | None = None,
    job_title: str | None = None,
    force: bool = False,
    owner: str | None = None,
    router: ProviderRouter | None = None,
    settings: Settings | None = None,
    conn: sqlite3.Connection | None = None,
) -> AnalysisResult:
    settings = settings or default_settings
    resume_text = (resume_text or "").strip()
    job_text = (job_text or "").strip()

    if len(resume_text) < MIN_INPUT_CHARS or len(job_text) < MIN_INPUT_CHARS:
        raise InputTooShort(
            f"both inputs need at least {MIN_INPUT_CHARS} characters of text"
        )

    started = time.monotonic()

    # ---------------------------------------------------------------- cache
    if not force:
        hit = cache.get_cached(resume_text, job_text, conn=conn, owner=owner)
        if hit is not None:
            return AnalysisResult(
                **hit, latency_ms=int((time.monotonic() - started) * 1000)
            )

    router = router or ProviderRouter(settings)
    log_ids: list[int] = []

    # -------------------------------------------------------------- analyst
    source = "ai"
    partial = False
    provider_used: str | None = None
    model_used: str | None = None

    try:
        run = AnalystAgent(router).run(resume_text=resume_text, job_text=job_text)
        analyst: AnalystOutput = run.output        # type: ignore[assignment]
        log_ids.extend(run.log_ids)
        provider_used, model_used = run.provider, run.model
    except (AllProvidersFailed, AgentError) as exc:
        # Every provider is unreachable, or none could produce valid JSON.
        # Degrade to keyword analysis rather than failing the request.
        if isinstance(exc, AllProvidersFailed):
            log_ids.extend(exc.log_ids)
        local = local_analyzer.analyze(resume_text, job_text)
        return _persist_and_build(
            resume_text=resume_text,
            job_text=job_text,
            resume_label=resume_label or _derive_resume_label(resume_text),
            job_title=job_title or _derive_job_title(job_text),
            score=local["overall_score"],
            score_breakdown=local["score_breakdown"],
            matched_skills=local["matched_skills"],
            missing_skills=local["missing_skills"],
            rewritten_bullets=[],
            suggestions=local["suggestions"],
            summary=local["summary"],
            provider_used=None,
            model_used=None,
            source="local_fallback",
            partial=False,
            log_ids=log_ids,
            latency_ms=int((time.monotonic() - started) * 1000),
            owner=owner,
            conn=conn,
        )

    # ------------------------------------------------------------- rewriter
    rewriter = RewriterOutput()
    try:
        rewrite_run = RewriterAgent(router).run(
            resume_text=resume_text,
            job_text=job_text,
            missing_skills=analyst.missing_skills,
        )
        rewriter = rewrite_run.output              # type: ignore[assignment]
        log_ids.extend(rewrite_run.log_ids)
        rewriter.suggestions = _top_up_suggestions(
            rewriter.suggestions, analyst.missing_skills
        )
    except (AllProvidersFailed, AgentError) as exc:
        # Scoring worked; rewriting did not. Return what we have and say so.
        if isinstance(exc, AllProvidersFailed):
            log_ids.extend(exc.log_ids)
        partial = True
        # A partial result would otherwise render an empty "What to do next"
        # card. Gap-derived advice is still true and still useful.
        rewriter.suggestions = _top_up_suggestions([], analyst.missing_skills)

    return _persist_and_build(
        resume_text=resume_text,
        job_text=job_text,
        resume_label=resume_label or _derive_resume_label(resume_text),
        job_title=job_title or _derive_job_title(job_text),
        score=analyst.overall_score,
        score_breakdown=analyst.score_breakdown.model_dump(),
        matched_skills=analyst.matched_skills,
        missing_skills=analyst.missing_skills,
        rewritten_bullets=[b.model_dump() for b in rewriter.rewritten_bullets],
        suggestions=rewriter.suggestions,
        summary=analyst.summary,
        provider_used=provider_used,
        model_used=model_used,
        source=source,
        partial=partial,
        log_ids=log_ids,
        latency_ms=int((time.monotonic() - started) * 1000),
        owner=owner,
        conn=conn,
    )


def _persist_and_build(
    *,
    resume_text: str,
    job_text: str,
    resume_label: str,
    job_title: str,
    score: int,
    score_breakdown: dict,
    matched_skills: list[str],
    missing_skills: list[str],
    rewritten_bullets: list[dict],
    suggestions: list[str],
    summary: str,
    provider_used: str | None,
    model_used: str | None,
    source: str,
    partial: bool,
    log_ids: list[int],
    latency_ms: int,
    conn: sqlite3.Connection | None,
    owner: str | None = None,
) -> AnalysisResult:
    """Write the analysis, backfill the provider log FKs, return the result."""
    resume_id = db.insert_resume(resume_label, resume_text, conn=conn)
    job_id = db.insert_job(job_title, job_text, conn=conn)
    input_hash = cache.input_hash(resume_text, job_text, owner)

    analysis_id = db.upsert_analysis(
        resume_id=resume_id,
        job_id=job_id,
        input_hash=input_hash,
        score=score,
        score_breakdown=score_breakdown,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        rewritten_bullets=rewritten_bullets,
        suggestions=suggestions,
        summary=summary,
        provider_used=provider_used,
        model_used=model_used,
        source=source,
        partial=partial,
        owner=owner,
        conn=conn,
    )

    # Attempts are logged as they happen - necessarily before the analysis row
    # exists - so the foreign key is filled in now.
    db.attach_logs_to_analysis(log_ids, analysis_id, conn=conn)

    return AnalysisResult(
        id=analysis_id,
        score=score,
        score_breakdown=score_breakdown,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        rewritten_bullets=rewritten_bullets,
        suggestions=suggestions,
        summary=summary,
        provider_used=provider_used,
        model_used=model_used,
        source=source,          # type: ignore[arg-type]
        partial=partial,
        cached=False,
        latency_ms=latency_ms,
        resume_label=resume_label,
        job_title=job_title,
    )
