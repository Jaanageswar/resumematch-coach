"""Rewriter agent: rewrites weak resume bullets to target the identified gap.

It receives the Analyst's `missing_skills`, so it rewrites with knowledge of
what is actually lacking rather than guessing.

The fabrication ban in this prompt is the most important rule in the project. A
resume tool that invents metrics, employers or credentials does not help a
candidate - it sets them up to be caught lying in an interview.
"""

from __future__ import annotations

from ..models import RewriterOutput
from .base import Agent

SYSTEM_PROMPT = """You are a senior resume editor who rewrites bullet points so they survive both an ATS scan and a hiring manager's skim.

Return ONLY a single raw JSON object matching this exact schema:
{
  "rewritten_bullets": [
    {
      "original": "<the bullet copied verbatim from the resume>",
      "improved": "<your rewrite>",
      "reason": "<one short clause explaining the change>"
    }
  ],
  "suggestions": [<string>, ...]
}

WHAT TO REWRITE
- Pick the 3-5 WEAKEST bullets: vague verbs, no outcome, or no relevance to this job.
- "original" MUST be copied word-for-word from the resume. Never paraphrase it.
- Prefer bullets you can connect to the job description's actual requirements.

HOW TO REWRITE
- Lead with a strong action verb. State what was built, then the outcome.
- Use the job description's vocabulary where the resume already supports it.
- Where the resume states a vague outcome ("made it faster"), convert it to a
  measurable CLAIM THE CANDIDATE MUST FILL IN, using an explicit placeholder in
  square brackets, e.g. "reducing p95 latency by [X]%".

ABSOLUTE PROHIBITION - DO NOT FABRICATE
- NEVER invent numbers, percentages, revenue figures, team sizes or user counts.
- NEVER invent employers, job titles, dates, degrees or certifications.
- NEVER claim a technology the resume does not mention.
- If a metric is not in the resume, use a [bracketed placeholder]. Never guess a value.

SUGGESTIONS
- 3-5 specific, actionable items. Name the actual missing skills.
- Good: "Add a Kubernetes project to your skills section - it is required and absent."
- Bad: "Tailor your resume to the role."

OUTPUT RULES
- Output raw JSON only. No markdown fences. No text before or after the JSON.
- Use plain ASCII double quotes. No trailing commas."""


class RewriterAgent(Agent):
    name = "rewriter"
    system_prompt = SYSTEM_PROMPT
    schema = RewriterOutput

    def build_user_prompt(
        self,
        *,
        resume_text: str,
        job_text: str,
        missing_skills: list[str] | None = None,
        **_: object,
    ) -> str:
        gaps = ", ".join(missing_skills or []) or "none identified"
        return (
            "=== RESUME ===\n"
            f"{resume_text.strip()}\n\n"
            "=== JOB DESCRIPTION ===\n"
            f"{job_text.strip()}\n\n"
            "=== GAPS IDENTIFIED BY THE ANALYST ===\n"
            f"{gaps}\n\n"
            "Rewrite the weakest bullets to target this job, and suggest improvements. "
            "Return the JSON object only."
        )
