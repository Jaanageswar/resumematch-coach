"""Job suggestions: which roles fit this resume, and where to find them.

The design decision that matters here: **we never show a job posting.** An LLM
asked for "jobs for this resume" will happily invent "Senior Engineer at Acme,
apply here", and a candidate who applies to a company that does not exist has
been harmed, not helped.

So the model's job is narrower and honest - name the *roles* this resume is a
realistic fit for, and why. We then build live search links to real job boards
for each role, so everything the user clicks through to is an actual listing.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from urllib.parse import urlencode

from pydantic import BaseModel

from . import db
from .agents.base import Agent, AgentError
from .cache import normalise
from .fallback import local_analyzer
from .models import JobLink, JobRole, JobsResponse, JobSuggestionsOutput
from .providers.router import AllProvidersFailed, ProviderRouter

CACHE_VERSION = "jobs-v1"

SYSTEM_PROMPT = """You are a technical career advisor. Given a resume, name the job roles it is a realistic fit for TODAY.

Return ONLY a single raw JSON object matching this exact schema:
{
  "location": "<the candidate's city and country exactly as the resume states it, or null>",
  "roles": [
    {
      "title": "<a job title exactly as employers post it on job boards>",
      "seniority": "<junior | mid | senior | lead>",
      "match": <integer 0-100>,
      "why": "<one sentence naming the specific resume evidence>",
      "keywords": [<3-5 skills FROM THE RESUME that recruiters search for>]
    }
  ]
}

RULES
- Return 4 to 6 roles, best fit first. Include at least one adjacent role the
  candidate could realistically move into, not only their current title.
- match = how well THIS resume fits typical postings for that role. Be honest.
  A role that needs skills the resume lacks scores below 60.
- Seniority must match the experience the resume actually shows.
- keywords must come from the resume. Never list a skill it does not mention.
- NEVER invent companies, specific job postings, salaries or URLs.

OUTPUT RULES
- Raw JSON only. No markdown fences. No text before or after the JSON.
- Plain ASCII double quotes. No trailing commas."""


class JobsAgent(Agent):
    name = "jobs"
    system_prompt = SYSTEM_PROMPT
    schema: type[BaseModel] = JobSuggestionsOutput

    def build_user_prompt(self, *, resume_text: str, **_: object) -> str:
        return (
            "=== RESUME ===\n"
            f"{resume_text.strip()}\n\n"
            "Name the roles this resume fits. Return the JSON object only."
        )


# --------------------------------------------------------------------------- #
# location
# --------------------------------------------------------------------------- #
INDIAN_CITIES = {
    "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "hyderabad",
    "chennai", "pune", "kolkata", "gurgaon", "gurugram", "noida", "ahmedabad",
    "kochi", "jaipur", "coimbatore", "chandigarh", "indore", "thiruvananthapuram",
    "visakhapatnam", "mysuru", "mysore", "bhubaneswar", "nagpur", "lucknow",
}

# Naukri's URL slugs use the older city names.
NAUKRI_CITY = {"bengaluru": "bangalore", "gurugram": "gurgaon", "mysuru": "mysore"}

_CITY_COUNTRY = re.compile(r"^[A-Z][A-Za-z .'-]{1,40},\s*[A-Z][A-Za-z .'-]{1,40}$")


def extract_location(resume_text: str) -> str | None:
    """Find "City, Country" in the resume header without an LLM.

    Headers are usually one or two lines of fields separated by pipes or
    bullets: "Backend Engineer | Bengaluru, India | me@example.com".
    """
    lines = [ln.strip() for ln in (resume_text or "").splitlines() if ln.strip()]
    for line in lines[:5]:
        for field in re.split(r"\s*[|•·]\s*|\s{3,}", line):
            field = field.strip()
            if "@" in field or "." in field.split(",")[0] or "http" in field:
                continue
            if _CITY_COUNTRY.match(field):
                return field
            if field.lower() in INDIAN_CITIES:
                return field
    return None


def _city(location: str | None) -> str:
    return (location or "").split(",")[0].strip()


def _in_india(location: str | None) -> bool:
    lowered = (location or "").lower()
    return "india" in lowered or _city(location).lower() in INDIAN_CITIES


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------- #
# links
# --------------------------------------------------------------------------- #
def links_for(title: str, location: str | None) -> list[JobLink]:
    """Live search links for one role.

    Every URL is a search, not a posting, so the user only ever lands on real,
    current listings that the board itself is showing.
    """
    city = _city(location)
    india = _in_india(location)
    links = [
        JobLink(
            board="LinkedIn",
            url="https://www.linkedin.com/jobs/search/?"
            + urlencode({"keywords": title, "location": location or "Worldwide"}),
        ),
        JobLink(
            board="LinkedIn · Remote",
            url="https://www.linkedin.com/jobs/search/?"
            + urlencode({"keywords": title, "f_WT": "2"}),
        ),
        JobLink(
            board="Indeed",
            url=("https://in.indeed.com/jobs?" if india else "https://www.indeed.com/jobs?")
            + urlencode({"q": title, "l": city}),
        ),
    ]
    if india:
        # Naukri is India-only; offering it elsewhere would just be a dead end.
        city_slug = NAUKRI_CITY.get(city.lower(), _slug(city))
        path = f"{_slug(title)}-jobs" + (f"-in-{city_slug}" if city_slug else "")
        links.append(JobLink(board="Naukri", url=f"https://www.naukri.com/{path}"))
    links.append(
        JobLink(
            board="Google Jobs",
            url="https://www.google.com/search?"
            + urlencode({
                "q": f"{title} jobs" + (f" in {city}" if city else ""),
                "ibp": "htl;jobs",
            }),
        )
    )
    return links


# --------------------------------------------------------------------------- #
# local fallback
# --------------------------------------------------------------------------- #
# Skill families -> the role titles employers actually post for them.
ROLE_FAMILIES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Backend Engineer", ("python", "fastapi", "django", "flask", "node.js",
                          "java", "go", "postgresql", "rest", "microservices"),
     "server-side services and APIs"),
    ("Python Developer", ("python", "fastapi", "django", "flask", "pytest", "sql"),
     "Python application development"),
    ("Platform / DevOps Engineer", ("docker", "kubernetes", "terraform", "aws",
                                    "ci/cd", "github actions", "linux", "helm"),
     "infrastructure, deployment and reliability"),
    ("Full-Stack Developer", ("react", "javascript", "typescript", "node.js",
                              "html", "css", "python", "rest"),
     "both frontend and backend work"),
    ("Frontend Engineer", ("react", "vue", "angular", "typescript", "javascript",
                           "html", "css", "next.js"),
     "user-facing web applications"),
    ("Data Engineer", ("sql", "spark", "airflow", "kafka", "etl", "dbt",
                       "postgresql", "python", "data pipeline"),
     "data pipelines and warehousing"),
    ("Machine Learning Engineer", ("python", "pytorch", "tensorflow",
                                   "scikit-learn", "machine learning", "pandas",
                                   "numpy", "llm"),
     "building and shipping ML models"),
    # Without this, a data scientist's resume was only offered Data Engineer
    # and ML Engineer - never the job they actually do.
    ("Data Scientist", ("python", "pandas", "numpy", "scikit-learn", "sql", "r",
                        "machine learning", "data modeling", "deep learning"),
     "statistical modelling and experimentation"),
    ("Java Developer", ("java", "spring", "spring boot", "sql", "microservices"),
     "Java services"),
)

_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)


def _seniority(resume_text: str) -> str:
    years = max((int(y) for y in _YEARS.findall(resume_text or "")), default=0)
    if years >= 8:
        return "lead"
    if years >= 5:
        return "senior"
    if years >= 2:
        return "mid"
    return "junior" if years else "mid"


def local_roles(resume_text: str) -> list[JobRole]:
    """Deterministic role matching from the skills the resume contains.

    Each role family scores by how many of its signature skills are present.
    It is cruder than the model, but it is honest about the same thing - fit
    to a role, never a specific posting.
    """
    present = set(local_analyzer.find_skills(resume_text))
    seniority = _seniority(resume_text)
    prefix = {"senior": "Senior ", "lead": "Lead "}.get(seniority, "")

    scored: list[JobRole] = []
    for title, signature, area in ROLE_FAMILIES:
        hits = [s for s in signature if s in present]
        if len(hits) < 2:
            continue
        # Three signature skills is already a credible fit; cap below 90
        # because a keyword count cannot justify more confidence than that.
        match = min(88, 30 + 15 * len(hits))
        shown = [local_analyzer._display(s) for s in hits[:5]]
        scored.append(JobRole(
            title=f"{prefix}{title}" if prefix and not title.startswith(prefix) else title,
            seniority=seniority,
            match=match,
            why=f"Your resume shows {', '.join(shown[:3])}, which is the core of "
                f"{area}.",
            keywords=shown,
        ))
    scored.sort(key=lambda r: (-r.match, r.title))
    return scored[:5]


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def jobs_hash(resume_text: str) -> str:
    return hashlib.sha256(
        f"{normalise(resume_text)}||{CACHE_VERSION}".encode("utf-8")
    ).hexdigest()


def cached(resume_text: str, conn: sqlite3.Connection | None = None) -> JobsResponse | None:
    payload = db.get_job_suggestions(jobs_hash(resume_text), conn=conn)
    if payload is None:
        return None
    return JobsResponse(**{**payload, "cached": True})


def suggest(
    resume_text: str,
    *,
    router: ProviderRouter,
    refresh: bool = False,
    conn: sqlite3.Connection | None = None,
) -> JobsResponse:
    started = time.monotonic()

    if not refresh:
        hit = cached(resume_text, conn=conn)
        if hit is not None:
            hit.latency_ms = int((time.monotonic() - started) * 1000)
            return hit

    source, provider, model = "ai", None, None
    try:
        run = JobsAgent(router).run(resume_text=resume_text)
        output: JobSuggestionsOutput = run.output          # type: ignore[assignment]
        roles, location = output.roles, output.location
        provider, model = run.provider, run.model
        if not roles:
            raise AgentError("model returned no roles")
    except (AllProvidersFailed, AgentError):
        roles, location, source = local_roles(resume_text), None, "local_fallback"

    # Trust the resume over the model for location when the model had none.
    location = location or extract_location(resume_text)
    for role in roles:
        role.links = links_for(role.title, location)

    response = JobsResponse(
        location=location,
        roles=roles,
        source=source,           # type: ignore[arg-type]
        provider_used=provider,
        model_used=model,
        cached=False,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    # Only cache AI results: a keyword fallback should be retried next time.
    if source == "ai":
        db.save_job_suggestions(
            jobs_hash(resume_text),
            response.model_dump(exclude={"cached", "latency_ms"}),
            conn=conn,
        )
    return response
