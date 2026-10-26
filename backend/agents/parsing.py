"""Extract a JSON object from whatever an LLM actually returned.

Free-tier models are told "JSON only, no fences" and comply most of the time.
The rest of the time they wrap the object in ```json fences, add "Here is the
analysis:" first, trail a comma, or use smart quotes because the prose model
underneath them likes typography.

This is a five-stage ladder, cheapest first. Stage 3 is the important one: a
regex like `\\{.*\\}` fails on nested objects, which our schema always has
(`score_breakdown`), so we count braces instead.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ParseError(ValueError):
    """No JSON object could be recovered from the text."""


_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_SMART_QUOTES = {
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‘": "'", "’": "'", "«": '"', "»": '"',
}


def strip_fences(text: str) -> str:
    """Remove a leading ```json and a trailing ``` if present."""
    out = _FENCE.sub("", text.strip())
    # A fence can also appear mid-string when the model adds a trailing note.
    if "```" in out:
        out = out.split("```", 1)[0]
    return out.strip()


def extract_balanced_object(text: str) -> str | None:
    """Return the first complete, brace-balanced {...} block.

    Brace counting rather than a regex, because the payload contains nested
    objects. Quote-aware so a `{` inside a string value does not throw the
    depth off, and escape-aware so `\\"` inside a string does not end it.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start:index + 1]
    return None


def repair(text: str) -> str:
    """Conservative fixes for the two mistakes models actually make."""
    for bad, good in _SMART_QUOTES.items():
        text = text.replace(bad, good)
    return _TRAILING_COMMA.sub(r"\1", text)


def extract_json(text: str) -> dict[str, Any]:
    """Run the ladder. Raises ParseError if every stage fails."""
    if not text or not text.strip():
        raise ParseError("empty response")

    candidates: list[str] = []

    def consider(candidate: str | None) -> None:
        if candidate and candidate.strip() and candidate not in candidates:
            candidates.append(candidate)

    # Stage 1 + 2: raw, then de-fenced.
    consider(text)
    defenced = strip_fences(text)
    consider(defenced)
    # Stage 3: first balanced object, from both the raw and de-fenced forms.
    consider(extract_balanced_object(defenced))
    consider(extract_balanced_object(text))
    # Stage 4: light repair of each candidate so far.
    for candidate in list(candidates):
        consider(repair(candidate))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        # A model occasionally returns [ {...} ] - unwrap a single-item list.
        if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
            return parsed[0]

    raise ParseError(
        f"no JSON object found in {len(text)} chars "
        f"(starts: {text.strip()[:80]!r})"
    )
