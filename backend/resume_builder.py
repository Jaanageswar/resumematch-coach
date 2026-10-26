"""Apply the Rewriter's bullets back into the resume, and render it as a PDF.

Two separate jobs, deliberately kept apart:

1. `apply_rewrites` - the actual edit. Each rewritten bullet replaces the line
   it came from, keeping that line's indentation and bullet marker. This is the
   part with real logic, and it is what makes the download a *tailored resume*
   rather than a reprint of the original.

2. `build_pdf` - typesetting. Infers structure from the plain text (name,
   contact block, section headings, bullets) and lays it out like a resume
   instead of a wall of monospace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fpdf import FPDF

# Characters an LLM emits that the PDF core fonts cannot encode. Mapped rather
# than dropped, so "reduced p95 latency by 38%" never becomes "reduced p95 ...".
_TRANSLATE = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", "•": "-",
    " ": " ", "→": "->", "·": "-", "′": "'",
    "″": '"', "​": "", "﻿": "",
}

_BULLET_PREFIX = re.compile(r"^(\s*)([-*•·o]\s+)(.*)$")
_WS = re.compile(r"\s+")


def _ascii(text: str) -> str:
    """Make text safe for the PDF core fonts without losing meaning."""
    for bad, good in _TRANSLATE.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def _norm(text: str) -> str:
    return _WS.sub(" ", text or "").strip().lower()


@dataclass
class RewriteReport:
    text: str
    applied: int
    skipped: list[str]


def apply_rewrites(resume_text: str, bullets: list[dict]) -> RewriteReport:
    """Replace each original bullet with its improved version.

    Matching is tolerant because the model echoes the bullet's *content* while
    the resume line also carries a marker and indentation: a line reading
    "  - Built APIs for internal tools" must match an `original` of
    "Built APIs for internal tools" and come back as "  - <improved>".

    Anything that cannot be located is reported rather than silently dropped -
    quietly failing to apply a rewrite would be worse than saying so.
    """
    lines = (resume_text or "").splitlines()
    applied = 0
    skipped: list[str] = []

    for bullet in bullets or []:
        original = (bullet.get("original") or "").strip()
        improved = (bullet.get("improved") or "").strip()
        if not original or not improved:
            continue

        target = _norm(original)
        hit = False

        # Pass 1: a line whose content matches exactly once markers are stripped.
        for i, line in enumerate(lines):
            match = _BULLET_PREFIX.match(line)
            content = match.group(3) if match else line.strip()
            if _norm(content) != target:
                continue
            if match:
                lines[i] = f"{match.group(1)}{match.group(2)}{improved}"
            else:
                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = f"{indent}{improved}"
            applied += 1
            hit = True
            break

        # Pass 2: the model trimmed or extended the text slightly.
        if not hit:
            for i, line in enumerate(lines):
                match = _BULLET_PREFIX.match(line)
                content = match.group(3) if match else line.strip()
                norm_content = _norm(content)
                if not norm_content or len(norm_content) < 12:
                    continue
                if target in norm_content or norm_content in target:
                    if match:
                        lines[i] = f"{match.group(1)}{match.group(2)}{improved}"
                    else:
                        indent = line[: len(line) - len(line.lstrip())]
                        lines[i] = f"{indent}{improved}"
                    applied += 1
                    hit = True
                    break

        if not hit:
            skipped.append(original)

    # splitlines() discards the final newline; restore it so that applying no
    # rewrites returns the resume byte-identical.
    rebuilt = "\n".join(lines)
    if (resume_text or "").endswith("\n"):
        rebuilt += "\n"
    return RewriteReport(rebuilt, applied, skipped)


def _looks_like_heading(line: str) -> bool:
    """A resume section heading: short, and essentially all caps."""
    stripped = line.strip()
    if not (2 < len(stripped) <= 42):
        return False
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) < 3:
        return False
    return all(c.isupper() for c in letters)


class _ResumePDF(FPDF):
    """A4 with a quiet footer. No header, so the name sits at the top."""

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(140, 140, 140)
        self.cell(0, 6, f"Tailored with ResumeMatch Coach  ·  page {self.page_no()}",
                  align="C")


def build_pdf(resume_text: str, *, job_title: str | None = None) -> bytes:
    """Render resume text as a typeset PDF.

    Structure is inferred, not configured: the first non-empty line is the
    name, the lines before the first blank are the contact block, all-caps
    lines are section headings, and marker lines are bullets.
    """
    pdf = _ResumePDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    pdf.set_title("Tailored resume")
    pdf.set_creator("ResumeMatch Coach")

    width = pdf.w - pdf.l_margin - pdf.r_margin
    lines = [ln.rstrip() for ln in _ascii(resume_text or "").splitlines()]

    # --- name -------------------------------------------------------------
    cursor = 0
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor < len(lines):
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(28, 36, 52)
        pdf.multi_cell(width, 9, lines[cursor].strip())
        cursor += 1

    # --- contact block: everything up to the first blank line -------------
    contact: list[str] = []
    while cursor < len(lines) and lines[cursor].strip():
        contact.append(lines[cursor].strip())
        cursor += 1
    if contact:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 100, 120)
        pdf.multi_cell(width, 4.6, "  |  ".join(contact))
    pdf.ln(2.5)
    pdf.set_draw_color(176, 125, 43)      # brass rule under the header
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
    pdf.ln(4)

    # --- body -------------------------------------------------------------
    for raw in lines[cursor:]:
        line = raw.strip()
        if not line:
            pdf.ln(2.2)
            continue

        if _looks_like_heading(line):
            pdf.ln(2.4)
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.set_text_color(176, 125, 43)
            pdf.multi_cell(width, 5.2, line)
            y = pdf.get_y() + 0.6
            pdf.set_draw_color(228, 221, 209)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, y, pdf.l_margin + width, y)
            pdf.ln(2.6)
            continue

        match = _BULLET_PREFIX.match(raw)
        if match:
            pdf.set_font("Helvetica", "", 9.8)
            pdf.set_text_color(40, 48, 64)
            indent = 4.0
            pdf.set_x(pdf.l_margin + indent)
            pdf.cell(3.2, 4.8, "-")
            pdf.set_x(pdf.l_margin + indent + 3.2)
            pdf.multi_cell(width - indent - 3.2, 4.8, match.group(3).strip())
            pdf.ln(0.6)
            continue

        # A line like "Backend Engineer - Nexora Logistics (Feb 2023 - Present)"
        # is a job title; bolding it is what makes the page scannable.
        is_role = bool(re.search(r"\(.*\d{4}.*\)\s*$", line)) or " - " in line[:60]
        pdf.set_font("Helvetica", "B" if is_role else "", 10 if is_role else 9.8)
        pdf.set_text_color(28, 36, 52) if is_role else pdf.set_text_color(40, 48, 64)
        pdf.multi_cell(width, 5.0, line)
        pdf.ln(0.4)

    return bytes(pdf.output())
