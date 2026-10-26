"""Deterministic keyword analyzer - the last line of defence.

When every provider is unreachable, this still answers. No AI, no network, no
dependencies: tokenise both documents, work out which terms the job description
demands, and check which of them the resume actually contains.

It is honest about what it is. The result carries `source="local_fallback"` so
the UI can say plainly that this is keyword analysis, not an AI review.

Determinism is a requirement, not a nicety: the same inputs must produce a
byte-identical result every time, so output is always sorted and nothing here
touches `random`, `set` iteration order, or the clock.
"""

from __future__ import annotations

import re
from collections import Counter

# Curated technical vocabulary. Its job is to separate real skills from generic
# job-posting noise: "stakeholder" and "fast-paced" are frequent in a JD but are
# not skills, and listing them as gaps would be useless advice.
SKILL_VOCAB: tuple[str, ...] = (
    # languages
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++",
    "c#", "ruby", "php", "scala", "kotlin", "swift", "sql", "bash", "shell",
    "r", "matlab", "perl", "elixir", "haskell", "dart", "objective-c",
    # web / backend
    "fastapi", "flask", "django", "express", "node.js", "nestjs", "spring",
    "spring boot", "rails", "laravel", "asp.net", ".net", "graphql", "rest",
    "rest api", "grpc", "protocol buffers", "websockets", "soap", "openapi",
    "microservices", "serverless", "event-driven", "api design",
    # frontend
    "react", "vue", "angular", "svelte", "next.js", "nuxt", "redux", "jquery",
    "html", "css", "sass", "tailwind", "webpack", "vite",
    # data stores
    "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis",
    "cassandra", "dynamodb", "elasticsearch", "neo4j", "clickhouse",
    "snowflake", "bigquery", "redshift", "oracle", "mariadb", "firebase",
    # data / ml
    "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow", "keras",
    "spark", "hadoop", "airflow", "dbt", "kafka", "rabbitmq", "celery",
    "etl", "data pipeline", "machine learning", "deep learning", "nlp",
    "computer vision", "llm", "rag", "vector database", "feature engineering",
    "data modeling", "data warehouse",
    # cloud / infra
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "helm",
    "terraform", "ansible", "pulumi", "cloudformation", "jenkins", "circleci",
    "github actions", "gitlab ci", "argocd", "linux", "unix", "nginx",
    "apache", "ci/cd", "infrastructure as code", "eks", "ecs", "lambda",
    "s3", "rds", "ec2", "iam", "vpc", "cloudwatch", "load balancing",
    # observability / ops
    "prometheus", "grafana", "datadog", "opentelemetry", "distributed tracing",
    "observability", "monitoring", "logging", "sre", "on-call", "incident response",
    "load testing", "capacity planning", "performance tuning",
    # practice
    "git", "agile", "scrum", "kanban", "tdd", "unit testing", "integration testing",
    "pytest", "jest", "selenium", "cypress", "code review", "pair programming",
    "system design", "distributed systems", "scalability", "high availability",
    "security", "oauth", "jwt", "encryption", "hipaa", "gdpr", "soc 2",
    "mentoring", "technical leadership", "documentation",
)

STOPWORDS: frozenset[str] = frozenset("""
a an and are as at be been being but by for from had has have he her his if in
into is it its of on or our she that the their them then there these they this
to was we were what when where which who will with would you your i me my not
no nor so than too very can could should do does did doing done more most other
some such only own same s t just now also about above after again against all
any because before below between both during each few further here how once
during own over under up down out off while why
will shall may might must able
role roles job jobs work working works worked experience experiences year years
team teams company companies position candidate candidates ideal strong good
great excellent ability abilities skill skills skilled knowledge understanding
looking seeking join help helping build building built design designing
develop developing developed maintain maintaining use using used new well
across within etc plus etc. including include includes required require
requires requirements responsibilities qualifications preferred nice must
we're you'll you're our us their you i'm it's don't
""".split())

# Terms that are technical-sounding but are not skills to recommend learning.
_NOISE = frozenset({"fast-paced", "stakeholder", "stakeholders", "business",
                    "customer", "customers", "product", "products"})

_WORD = re.compile(r"[a-z0-9][a-z0-9+#./_-]*")
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)")

TOP_JD_TERMS = 40


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def tokenise(text: str) -> list[str]:
    """Lowercase word tokens, stopwords removed, order preserved."""
    return [
        token for token in _WORD.findall(_normalise(text))
        if token not in STOPWORDS and len(token) > 1
    ]


def term_counts(text: str) -> Counter[str]:
    """Unigrams plus bigrams.

    Bigrams matter: "machine learning", "infrastructure as code" and "event
    driven" are single concepts that unigrams would shred into useless pieces.
    """
    tokens = tokenise(text)
    counts: Counter[str] = Counter(tokens)
    counts.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
    return counts


def _skill_pattern(skill: str) -> re.Pattern[str]:
    """Match a skill as a whole term.

    `\\b` is unreliable at the edges of "c++", ".net" and "ci/cd" because +, .
    and / are not word characters, so the boundary is asserted manually.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])")


_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (skill, _skill_pattern(skill)) for skill in SKILL_VOCAB
)


def find_skills(text: str) -> list[str]:
    """Vocabulary skills present in the text, in vocabulary order."""
    haystack = _normalise(text)
    return [skill for skill, pattern in _SKILL_PATTERNS if pattern.search(haystack)]


def _display(skill: str) -> str:
    """Present a vocabulary entry the way a human writes it."""
    special = {
        "postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mysql": "MySQL",
        "sqlite": "SQLite", "mongodb": "MongoDB", "javascript": "JavaScript",
        "typescript": "TypeScript", "nodejs": "Node.js", "node.js": "Node.js",
        "next.js": "Next.js", "fastapi": "FastAPI", "graphql": "GraphQL",
        "grpc": "gRPC", "rest api": "REST API", "rest": "REST", "ci/cd": "CI/CD",
        "aws": "AWS", "gcp": "GCP", "iam": "IAM", "vpc": "VPC", "s3": "S3",
        "rds": "RDS", "ec2": "EC2", "eks": "EKS", "ecs": "ECS", "sql": "SQL",
        "etl": "ETL", "nlp": "NLP", "llm": "LLM", "rag": "RAG", "sre": "SRE",
        "tdd": "TDD", "html": "HTML", "css": "CSS", "jwt": "JWT",
        "oauth": "OAuth", "hipaa": "HIPAA", "gdpr": "GDPR", "soc 2": "SOC 2",
        "c++": "C++", "c#": "C#", ".net": ".NET", "asp.net": "ASP.NET",
        "github actions": "GitHub Actions", "gitlab ci": "GitLab CI",
        "cloudwatch": "CloudWatch", "event-driven": "event-driven",
        "infrastructure as code": "infrastructure as code",
        "on-call": "on-call", "node.js": "Node.js", "next.js": "Next.js",
        "distributed tracing": "distributed tracing",
        "capacity planning": "capacity planning", "machine learning": "machine learning",
        "data pipeline": "data pipeline", "api design": "API design",
        "system design": "system design", "unit testing": "unit testing",
        "integration testing": "integration testing", "load testing": "load testing",
        "incident response": "incident response", "high availability": "high availability",
        "distributed systems": "distributed systems", "technical leadership": "technical leadership",
        "protocol buffers": "Protocol Buffers", "vector database": "vector database",
        "data warehouse": "data warehouse", "data modeling": "data modeling",
        "feature engineering": "feature engineering", "spring boot": "Spring Boot",
        "google cloud": "Google Cloud", "load balancing": "load balancing",
        "code review": "code review", "pair programming": "pair programming",
        "deep learning": "deep learning", "computer vision": "computer vision",
        "opentelemetry": "OpenTelemetry", "argocd": "ArgoCD",
        "scikit-learn": "scikit-learn", "pytorch": "PyTorch",
        "tensorflow": "TensorFlow", "bigquery": "BigQuery",
        "clickhouse": "ClickHouse", "dynamodb": "DynamoDB",
        "elasticsearch": "Elasticsearch", "rabbitmq": "RabbitMQ",
        "kubernetes": "Kubernetes", "terraform": "Terraform", "docker": "Docker",
        "kafka": "Kafka", "redis": "Redis", "python": "Python", "java": "Java",
        "go": "Go", "golang": "Go", "rust": "Rust", "linux": "Linux",
        "git": "Git", "agile": "Agile", "scrum": "Scrum", "pytest": "pytest",
    }
    if skill in special:
        return special[skill]
    return skill.title()


def _rank_display(skills: list[str], job_text: str) -> list[str]:
    """Display form, de-duplicated, ordered by importance to the job.

    Importance = where the job description first mentions the skill. Postings
    are written in priority order - REQUIRED QUALIFICATIONS comes before NICE
    TO HAVE - so position is a far better signal than frequency, where almost
    every skill appears exactly once.

    Sorting alphabetically and then truncating to 12 was actively harmful: it
    reported "Apache, Capacity Planning, CloudWatch" as the top gaps for a role
    whose headline requirements were Kubernetes, Terraform and AWS.

    Ties break alphabetically, so the output stays deterministic.
    """
    haystack = _normalise(job_text)
    rank: dict[str, int] = {}
    for skill in skills:
        name = _display(skill)
        match = _skill_pattern(skill).search(haystack)
        position = match.start() if match else len(haystack)
        rank[name] = min(rank.get(name, position), position)
    return [name for name, _ in sorted(rank.items(), key=lambda kv: (kv[1], kv[0]))]


def _max_years(text: str) -> int:
    matches = _YEARS.findall(_normalise(text))
    return max((int(m) for m in matches), default=0)


def analyze(resume_text: str, job_text: str) -> dict:
    """Score the resume against the job description with no AI.

    Returns the same field names the Analyst agent produces, so the orchestrator
    can merge either one into an AnalysisResult without special-casing.
    """
    if not (resume_text or "").strip() or not (job_text or "").strip():
        # Nothing to compare. A non-zero score here would be pure invention.
        return {
            "overall_score": 0,
            "score_breakdown": {"skills": 0, "experience": 0, "keywords": 0},
            "matched_skills": [], "missing_skills": [],
            "summary": "No text to analyse.",
            "rewritten_bullets": [], "suggestions": [],
        }

    resume_skills = find_skills(resume_text)
    job_skills = find_skills(job_text)

    resume_set = set(resume_skills)
    matched_raw = [s for s in job_skills if s in resume_set]
    missing_raw = [s for s in job_skills if s not in resume_set]

    matched = _rank_display(matched_raw, job_text)
    missing = _rank_display(missing_raw, job_text)

    # --- skills component: share of the job's required skills that are present
    required_count = len({_display(s) for s in job_skills})
    skills_score = round(100 * len(matched) / required_count) if required_count else 0

    # --- keywords component: share of the JD's most frequent terms present
    job_terms = [term for term, _ in term_counts(job_text).most_common(TOP_JD_TERMS)
                 if term not in _NOISE]
    resume_terms = set(term_counts(resume_text))
    overlap = [term for term in job_terms if term in resume_terms]
    keywords_score = round(100 * len(overlap) / len(job_terms)) if job_terms else 0

    # --- experience component: years claimed vs years asked for
    resume_years = _max_years(resume_text)
    job_years = _max_years(job_text)
    if job_years and resume_years:
        experience_score = min(100, round(100 * resume_years / job_years))
    elif resume_years:
        experience_score = 70   # resume shows tenure, JD did not state a bar
    else:
        experience_score = 50   # nothing to go on either way

    overall = round(0.5 * skills_score + 0.3 * keywords_score + 0.2 * experience_score)
    overall = max(0, min(100, overall))

    summary = (
        f"Keyword analysis matched {len(matched)} of {required_count} "
        f"technical skills found in the job description"
        + (f", and {len(missing)} are missing ({', '.join(missing[:3])}"
           + ("..." if len(missing) > 3 else "") + ")." if missing else ".")
        + " This is a basic keyword comparison, not an AI review:"
        " AI providers were unavailable."
    )

    suggestions = _suggestions(missing, resume_years, job_years)
    # Only true on this path, where no AI answered.
    suggestions.append(
        "Re-run this analysis once an AI provider is available for rewritten "
        "bullet points and a semantic match score."
    )

    return {
        "overall_score": overall,
        "score_breakdown": {
            "skills": skills_score,
            "experience": experience_score,
            "keywords": keywords_score,
        },
        "matched_skills": matched[:20],
        "missing_skills": missing[:12],
        "summary": summary,
        "rewritten_bullets": [],
        "suggestions": suggestions,
    }


def fallback_suggestions(missing_skills: list[str]) -> list[str]:
    """Suggestions derived from the gap, with no AI involved.

    Used when the Rewriter succeeds at bullets but returns an empty
    `suggestions` list - which free models do occasionally. Showing the user an
    empty "Suggestions" heading is worse than showing them generic-but-true
    advice built from their actual missing skills.
    """
    return _suggestions(missing_skills, 0, 0)


def _suggestions(missing: list[str], resume_years: int, job_years: int) -> list[str]:
    """Canned but genuinely actionable advice, built from the actual gap."""
    out: list[str] = []
    if missing:
        out.append(
            "Add the missing required skills to your resume where you genuinely "
            f"have them: {', '.join(missing[:5])}."
        )
        out.append(
            f"'{missing[0]}' appears in the job description but not in your resume. "
            "If you have any exposure to it, name it explicitly in a bullet."
        )
    if job_years and resume_years and resume_years < job_years:
        out.append(
            f"The role asks for {job_years}+ years and your resume shows about "
            f"{resume_years}. Lead with scope and impact rather than tenure."
        )
    out.append(
        "Quantify your bullets. Replace vague outcomes like 'made it faster' with "
        "a concrete figure you can defend in an interview."
    )
    out.append(
        "Mirror the job description's exact terminology where it is truthful - "
        "ATS keyword matching is literal."
    )
    # Deliberately no "re-run once AI is available" line here: these are also
    # used to top up a successful AI result, where that advice would be false.
    # The local-fallback path adds it in `analyze()` instead.
    return out[:5]
