"""FastAPI application.

Binds to loopback only (see run.ps1 / DESIGN.md s17). No endpoint ever returns
a credential: /providers/status reports booleans and aggregates.

Every handler is wrapped by a global exception handler that returns JSON, so a
bug here surfaces in the UI as a readable sentence rather than an HTML
traceback.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import chat, db, jobs, security
from .agents.orchestrator import InputTooShort, analyze
from .config import ROOT, settings
from .extract import ExtractionError, extract_text
from .resume_builder import apply_rewrites, build_pdf
from .models import (MAX_CHAT_MESSAGE, AnalysisResult, AnalyzeRequest,
                     ChatRequest, ChatResponse, CompareResult, ExtractResult,
                     HealthResult, HistoryItem, JobsRequest, JobsResponse,
                     ProviderLogRow, ProviderStatus, SkillDiff, TrendPoint)
from .providers.router import ProviderRouter

logger = logging.getLogger("resumematch")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    logger.info(
        "ResumeMatch Coach %s ready. providers configured: %s",
        settings.version, ", ".join(settings.configured_providers) or "none",
    )
    yield


app = FastAPI(
    title="ResumeMatch Coach",
    version=settings.version,
    description="Multi-agent resume analysis with provider failover.",
    lifespan=lifespan,
)

# Password gate and AI request limits for a public deployment. Inert locally:
# with no APP_PASSWORD and no limits set, it passes every request straight
# through. Registered before CORS so CORS stays the outer layer (Starlette runs
# middleware in reverse order of registration).
app.middleware("http")(security.gate)

# The Vite dev server. Loopback origins only - this app is not meant to be
# reachable from anywhere else.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# One router for the process, so the circuit breaker's memory persists between
# requests. That is the whole point of it.
router = ProviderRouter(settings)


@app.exception_handler(Exception)
async def _unhandled(request: Request, _exc: Exception) -> JSONResponse:
    """Never leak a traceback to the browser."""
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on the server. "
                           "The analysis was not saved."},
    )


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResult)
def health() -> HealthResult:
    try:
        db.get_conn().execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return HealthResult(
        status="ok" if db_ok else "degraded",
        db=db_ok,
        version=settings.version,
        providers_configured=list(settings.configured_providers),
        provider_order=list(settings.provider_order),
    )


# --------------------------------------------------------------------------- #
# analyze
# --------------------------------------------------------------------------- #
def _validate_text(label: str, text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise HTTPException(422, f"{label} is empty.")
    if len(stripped) > settings.max_input_chars:
        raise HTTPException(
            422,
            f"{label} is {len(stripped):,} characters; the limit is "
            f"{settings.max_input_chars:,}. Please trim it.",
        )
    return stripped


@app.post("/analyze", response_model=AnalysisResult)
def post_analyze(payload: AnalyzeRequest, request: Request) -> AnalysisResult:
    resume_text = _validate_text("Resume", payload.resume_text)
    job_text = _validate_text("Job description", payload.job_text)

    try:
        return analyze(
            resume_text,
            job_text,
            resume_label=(payload.resume_label or "").strip() or None,
            job_title=(payload.job_title or "").strip() or None,
            force=payload.force,
            router=router,
            settings=settings,
            owner=security.owner_of(request),
        )
    except InputTooShort as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------------------------------------- #
# file extraction
# --------------------------------------------------------------------------- #
@app.post("/extract", response_model=ExtractResult)
async def post_extract(file: UploadFile = File(...)) -> ExtractResult:
    data = await file.read()
    try:
        text, pages = extract_text(data, file.filename or "")
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc

    if len(text) > settings.max_input_chars:
        text = text[: settings.max_input_chars]
    return ExtractResult(
        text=text, chars=len(text), pages=pages, filename=file.filename
    )


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #
@app.get("/history", response_model=list[HistoryItem])
def get_history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    job_title: str | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    source: str | None = Query(None, pattern="^(ai|local_fallback)$"),
) -> list[HistoryItem]:
    rows = db.list_analyses(
        limit=limit, job_title=job_title, min_score=min_score, source=source,
        owner=security.owner_of(request),
    )
    return [HistoryItem(**row) for row in rows]


@app.get("/analysis/{analysis_id}", response_model=AnalysisResult)
def get_analysis(analysis_id: int, request: Request) -> AnalysisResult:
    # Another visitor's id is a plain 404, never a 403, so probing ids reveals
    # nothing about which ones exist.
    row = db.get_analysis(analysis_id, owner=security.owner_of(request))
    if row is None:
        raise HTTPException(404, f"No analysis with id {analysis_id}.")
    return AnalysisResult(**row, cached=True)


@app.delete("/analysis/{analysis_id}", status_code=204, response_class=Response)
def remove_analysis(analysis_id: int, request: Request) -> Response:
    if not db.delete_analysis(analysis_id, owner=security.owner_of(request)):
        raise HTTPException(404, f"No analysis with id {analysis_id}.")
    # 204 must carry no body, so return an empty Response rather than None.
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #
@app.get("/compare", response_model=CompareResult)
def compare(request: Request, a: int = Query(...), b: int = Query(...)) -> CompareResult:
    if a == b:
        raise HTTPException(422, "Pick two different analyses to compare.")

    owner = security.owner_of(request)
    first, second = db.get_analysis(a, owner=owner), db.get_analysis(b, owner=owner)
    for analysis_id, row in ((a, first), (b, second)):
        if row is None:
            raise HTTPException(404, f"No analysis with id {analysis_id}.")
    assert first is not None and second is not None  # for type checkers

    # Case-insensitive comparison, but report the newer analysis's spelling.
    a_matched = {s.lower() for s in first["matched_skills"]}
    a_missing = {s.lower() for s in first["missing_skills"]}
    b_matched = {s.lower() for s in second["matched_skills"]}

    skills = SkillDiff(
        newly_matched=sorted(s for s in second["matched_skills"]
                             if s.lower() not in a_matched),
        still_missing=sorted(s for s in second["missing_skills"]
                             if s.lower() in a_missing),
        newly_missing=sorted(s for s in second["missing_skills"]
                             if s.lower() not in a_missing
                             and s.lower() not in b_matched),
    )

    # Trend over the newer analysis's job, so the chart has a single subject.
    job_row = db.get_conn().execute(
        "SELECT job_id FROM analyses WHERE id = ?", (b,)
    ).fetchone()
    trend = [
        TrendPoint(**point)
        for point in db.score_history(int(job_row["job_id"]), owner=owner)
    ] if job_row else []

    return CompareResult(
        a=AnalysisResult(**first, cached=True),
        b=AnalysisResult(**second, cached=True),
        score_delta=second["score"] - first["score"],
        skills=skills,
        trend=trend,
    )


# --------------------------------------------------------------------------- #
# Pip: chat and job suggestions
# --------------------------------------------------------------------------- #
def _resume_for(
    analysis_id: int | None, resume_text: str | None, owner: str | None = None
) -> tuple[str, dict | None, dict | None]:
    """Resolve the resume an endpoint should work on.

    An analysis id wins, because it pins the exact text that was scored. A raw
    resume_text covers the case where the user dropped a resume but has not
    analysed it yet.
    """
    analysis = sources = None
    if analysis_id is not None:
        analysis = db.get_analysis(analysis_id, owner=owner)
        sources = db.get_analysis_sources(analysis_id, owner=owner)
        if analysis is None or sources is None:
            raise HTTPException(404, f"No analysis with id {analysis_id}.")
        return sources["resume_text"], analysis, sources
    return (resume_text or "").strip(), None, None


@app.post("/chat", response_model=ChatResponse)
def post_chat(payload: ChatRequest, request: Request) -> ChatResponse:
    if not payload.messages:
        raise HTTPException(422, "Say something to Pip first.")
    last = payload.messages[-1]
    if last.role != "user" or not last.content:
        raise HTTPException(422, "The last message must be a non-empty question from you.")
    if len(last.content) > MAX_CHAT_MESSAGE:
        raise HTTPException(
            422, f"That message is {len(last.content):,} characters; "
                 f"please keep it under {MAX_CHAT_MESSAGE:,}.")

    resume_text, analysis, sources = _resume_for(
        payload.analysis_id, payload.resume_text, security.owner_of(request))
    resume_text = resume_text[: settings.max_input_chars]
    known_jobs = jobs.cached(resume_text) if resume_text else None

    return chat.reply(
        payload.messages,
        router=router,
        resume_text=resume_text or None,
        job_text=sources["job_text"] if sources else None,
        analysis=analysis,
        jobs=known_jobs,
    )


@app.post("/jobs", response_model=JobsResponse)
def post_jobs(payload: JobsRequest, request: Request) -> JobsResponse:
    resume_text, _, _ = _resume_for(
        payload.analysis_id, payload.resume_text, security.owner_of(request))
    if len(resume_text) < 30:
        raise HTTPException(422, "Drop or paste a resume first, so Pip knows what to match.")
    return jobs.suggest(
        resume_text[: settings.max_input_chars],
        router=router,
        refresh=payload.refresh,
    )


# --------------------------------------------------------------------------- #
# tailored resume export
# --------------------------------------------------------------------------- #
@app.get("/analysis/{analysis_id}/resume.pdf", include_in_schema=True)
def tailored_resume_pdf(analysis_id: int, request: Request) -> Response:
    """The user's resume with the Rewriter's improved bullets applied, as a PDF.

    This is an edit of their actual resume, not a report about it: each
    rewritten bullet replaces the line it came from, and the rest of the
    document is left exactly as they wrote it.
    """
    owner = security.owner_of(request)
    analysis = db.get_analysis(analysis_id, owner=owner)
    sources = db.get_analysis_sources(analysis_id, owner=owner)
    if analysis is None or sources is None:
        raise HTTPException(404, f"No analysis with id {analysis_id}.")

    bullets = analysis.get("rewritten_bullets") or []
    if not bullets:
        raise HTTPException(
            422,
            "This analysis has no rewritten bullets to apply, so a tailored "
            "resume would be identical to your original.",
        )

    report = apply_rewrites(sources["resume_text"], bullets)
    if report.applied == 0:
        raise HTTPException(
            422,
            "None of the rewritten bullets could be matched back to lines in "
            "your resume, so nothing would change.",
        )

    pdf = build_pdf(report.text, job_title=sources.get("job_title"))

    # A filename the user can recognise in their downloads folder.
    stem = re.sub(r"[^A-Za-z0-9]+", "-",
                  (sources.get("resume_label") or "resume")).strip("-").lower()
    filename = f"{stem or 'resume'}-tailored.pdf"

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Tell the UI how much actually changed, so it can be honest about
            # any bullet it could not place.
            "X-Rewrites-Applied": str(report.applied),
            "X-Rewrites-Skipped": str(len(report.skipped)),
        },
    )


# --------------------------------------------------------------------------- #
# observability
# --------------------------------------------------------------------------- #
@app.get("/providers/status", response_model=list[ProviderStatus])
def providers_status() -> list[ProviderStatus]:
    return [ProviderStatus(**row) for row in router.status()]


@app.get("/providers/logs", response_model=list[ProviderLogRow])
def providers_logs(limit: int = Query(20, ge=1, le=200)) -> list[ProviderLogRow]:
    return [
        ProviderLogRow(**{**row, "success": bool(row["success"])})
        for row in db.recent_provider_logs(limit)
    ]


# --------------------------------------------------------------------------- #
# the single-page app
#
# The UI is one static HTML file served from this same process, so the whole
# app is one command on one port. No build step, no node_modules, no second
# dev server to keep alive.
# --------------------------------------------------------------------------- #
# Prefer the built React app; fall back to the dependency-free single page.
# This means `npm run build` is optional - the app always has a working UI.
REACT_DIST = ROOT / "frontend-react" / "dist"
PLAIN_INDEX = ROOT / "frontend" / "index.html"
SAMPLE_DIR = ROOT / "sample_data"


def _index_file() -> Path:
    react_index = REACT_DIST / "index.html"
    return react_index if react_index.exists() else PLAIN_INDEX


@app.get("/sample")
def sample() -> dict[str, str]:
    """Sample resume and job description for the "Load sample" button."""
    try:
        return {
            "resume": (SAMPLE_DIR / "resume.txt").read_text(encoding="utf-8"),
            "job": (SAMPLE_DIR / "job_description.txt").read_text(encoding="utf-8"),
        }
    except OSError as exc:
        raise HTTPException(404, "Sample data not found.") from exc


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    index_file = _index_file()
    if not index_file.exists():  # pragma: no cover - defensive
        raise HTTPException(404, "No frontend found. Run: cd frontend-react && npm run build")
    # no-store so a rebuild shows up on reload during development
    return FileResponse(index_file, headers={"Cache-Control": "no-store"})


# Vite emits hashed filenames under /assets, so they are safe to cache hard.
if (REACT_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=REACT_DIST / "assets"), name="assets")
