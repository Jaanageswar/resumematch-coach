"""Pydantic schemas.

These are contracts in two directions:

- `AnalystOutput` / `RewriterOutput` validate what the LLM gave us. A model that
  returns syntactically valid JSON of the wrong shape has still failed, and the
  router needs to know that so it can fail over.
- The rest validate what we give the API client.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_BULLETS = 5
MAX_SKILLS = 40
MAX_SUGGESTIONS = 8


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _clamp(value: Any, low: int = 0, high: int = 100) -> int:
    """Coerce whatever the model produced into an int in range.

    Free-tier models return "85", 85.0, "80%", "about 75", "85/100", or
    occasionally 850. Clamping is kinder than rejecting: the score is advisory,
    and a hard failure here would throw away an otherwise good analysis.
    """
    if isinstance(value, bool):
        return low
    if isinstance(value, (int, float)):
        number = int(round(value))
    else:
        match = _NUMBER.search(str(value or ""))
        if not match:
            return low
        number = int(round(float(match.group())))
    return max(low, min(high, number))


def _clean_list(values: Any, limit: int) -> list[str]:
    """De-duplicate case-insensitively while preserving order and original case."""
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or len(text) > 80:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# agent contracts
# --------------------------------------------------------------------------- #
class ScoreBreakdown(BaseModel):
    skills: int = 0
    experience: int = 0
    keywords: int = 0

    @field_validator("skills", "experience", "keywords", mode="before")
    @classmethod
    def _clamp_each(cls, value: Any) -> int:
        return _clamp(value)


class AnalystOutput(BaseModel):
    overall_score: int = 0
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("overall_score", mode="before")
    @classmethod
    def _clamp_score(cls, value: Any) -> int:
        return _clamp(value)

    @field_validator("matched_skills", "missing_skills", mode="before")
    @classmethod
    def _clean_skills(cls, value: Any) -> list[str]:
        return _clean_list(value, MAX_SKILLS)

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_summary(cls, value: Any) -> str:
        return str(value or "").strip()[:1200]

    @model_validator(mode="after")
    def _keep_score_consistent(self) -> "AnalystOutput":
        """Derive the overall score from the breakdown when the model omits it.

        Some models fill the breakdown but leave overall_score at 0, which would
        show the user a confident "0" next to three healthy sub-scores.
        """
        parts = self.score_breakdown
        if self.overall_score == 0 and any((parts.skills, parts.experience, parts.keywords)):
            self.overall_score = _clamp(
                (parts.skills + parts.experience + parts.keywords) / 3
            )
        return self


class RewrittenBullet(BaseModel):
    original: str = ""
    improved: str = ""
    reason: str = ""

    @field_validator("original", "improved", "reason", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> str:
        return str(value or "").strip()[:600]


class RewriterOutput(BaseModel):
    rewritten_bullets: list[RewrittenBullet] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("rewritten_bullets", mode="before")
    @classmethod
    def _cap_bullets(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        return value[:MAX_BULLETS]

    @field_validator("suggestions", mode="before")
    @classmethod
    def _clean_suggestions(cls, value: Any) -> list[str]:
        return _clean_list(value, MAX_SUGGESTIONS)

    @model_validator(mode="after")
    def _drop_empty_bullets(self) -> "RewriterOutput":
        """A bullet with no improvement is noise - don't render an empty card."""
        self.rewritten_bullets = [
            bullet for bullet in self.rewritten_bullets
            if bullet.improved and bullet.original
        ]
        return self


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    resume_text: str
    job_text: str
    resume_label: str | None = None
    job_title: str | None = None
    force: bool = False


class AnalysisResult(BaseModel):
    id: int | None = None
    score: int
    score_breakdown: ScoreBreakdown
    matched_skills: list[str]
    missing_skills: list[str]
    rewritten_bullets: list[RewrittenBullet]
    suggestions: list[str]
    summary: str = ""
    provider_used: str | None = None
    model_used: str | None = None
    source: Literal["ai", "local_fallback"] = "ai"
    partial: bool = False
    cached: bool = False
    latency_ms: int | None = None
    resume_label: str | None = None
    job_title: str | None = None
    created_at: str | None = None


class HistoryItem(BaseModel):
    id: int
    score: int
    resume_label: str | None = None
    job_title: str | None = None
    provider_used: str | None = None
    model_used: str | None = None
    source: str = "ai"
    partial: bool = False
    created_at: str | None = None


class SkillDiff(BaseModel):
    newly_matched: list[str]
    still_missing: list[str]
    newly_missing: list[str]


class TrendPoint(BaseModel):
    id: int
    score: int
    created_at: str
    resume_label: str | None = None


class CompareResult(BaseModel):
    a: AnalysisResult
    b: AnalysisResult
    score_delta: int
    skills: SkillDiff
    trend: list[TrendPoint] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    provider: str
    model: str | None = None
    fallback_models: list[str] = Field(default_factory=list)
    configured: bool
    supports_json_mode: bool = True
    cooling_down: bool = False
    cooling_seconds_left: int = 0
    consecutive_failures: int = 0
    attempts: int = 0
    success_rate: float | None = None
    avg_latency_ms: int | None = None


class ProviderLogRow(BaseModel):
    id: int
    analysis_id: int | None = None
    provider: str
    model: str | None = None
    success: bool
    latency_ms: int | None = None
    error: str | None = None
    created_at: str


class ExtractResult(BaseModel):
    text: str
    chars: int
    pages: int | None = None
    filename: str | None = None


class HealthResult(BaseModel):
    status: str
    db: bool
    version: str
    providers_configured: list[str]
    provider_order: list[str]


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
MAX_CHAT_MESSAGE = 2000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

    @field_validator("content", mode="before")
    @classmethod
    def _trim(cls, value: Any) -> str:
        return str(value or "").strip()


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    analysis_id: int | None = None
    resume_text: str | None = None


class ChatResponse(BaseModel):
    reply: str
    source: Literal["ai", "local_fallback"] = "ai"
    provider_used: str | None = None
    model_used: str | None = None
    latency_ms: int | None = None


# --------------------------------------------------------------------------- #
# job suggestions
# --------------------------------------------------------------------------- #
class JobLink(BaseModel):
    board: str
    url: str


class JobRole(BaseModel):
    title: str = ""
    seniority: str = ""
    match: int = 0
    why: str = ""
    keywords: list[str] = Field(default_factory=list)
    links: list[JobLink] = Field(default_factory=list)

    @field_validator("match", mode="before")
    @classmethod
    def _clamp_match(cls, value: Any) -> int:
        return _clamp(value)

    @field_validator("title", "seniority", "why", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "").strip()[:300]

    @field_validator("keywords", mode="before")
    @classmethod
    def _clean_keywords(cls, value: Any) -> list[str]:
        return _clean_list(value, 6)


class JobSuggestionsOutput(BaseModel):
    """The LLM contract. Links are never taken from the model - we build them."""

    location: str | None = None
    roles: list[JobRole] = Field(default_factory=list)

    @field_validator("location", mode="before")
    @classmethod
    def _clean_location(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        if not text or text.lower() in {"null", "none", "unknown", "n/a", "remote"}:
            return None
        return text[:80]

    @model_validator(mode="after")
    def _keep_real_roles(self) -> "JobSuggestionsOutput":
        seen: set[str] = set()
        roles: list[JobRole] = []
        for role in self.roles:
            key = role.title.lower()
            if role.title and key not in seen:
                seen.add(key)
                roles.append(role)
        self.roles = sorted(roles, key=lambda r: -r.match)[:6]
        return self


class JobsRequest(BaseModel):
    resume_text: str | None = None
    analysis_id: int | None = None
    refresh: bool = False


class JobsResponse(BaseModel):
    location: str | None = None
    roles: list[JobRole]
    source: Literal["ai", "local_fallback"] = "ai"
    provider_used: str | None = None
    model_used: str | None = None
    cached: bool = False
    latency_ms: int | None = None
