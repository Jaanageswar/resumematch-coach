"""The JSON extraction ladder, and the Pydantic contracts it feeds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.parsing import (ParseError, extract_balanced_object,
                                    extract_json, strip_fences)
from backend.models import AnalystOutput, RewriterOutput

GOOD = {"overall_score": 70}


@pytest.mark.parametrize("raw", [
    '{"overall_score": 70}',
    '```json\n{"overall_score": 70}\n```',
    '```\n{"overall_score": 70}\n```',
    'Here is the analysis:\n{"overall_score": 70}',
    '{"overall_score": 70}\n\nHope that helps!',
    'Sure!\n```json\n{"overall_score": 70}\n```\nLet me know.',
    '{"overall_score": 70,}',
    '“overall_score” could be... {“overall_score”: 70}',
    '[{"overall_score": 70}]',
    '   \n {"overall_score": 70} \n  ',
])
def test_recovers_the_object(raw):
    assert extract_json(raw)["overall_score"] == 70


def test_nested_objects_survive():
    """A regex like \\{.*\\} truncates here; brace counting does not."""
    raw = ('prefix {"overall_score": 70, "score_breakdown": '
           '{"skills": 80, "experience": 60, "keywords": 70}} suffix')
    got = extract_json(raw)
    assert got["score_breakdown"]["keywords"] == 70


def test_braces_inside_a_string_do_not_break_counting():
    raw = '{"summary": "uses {curly} braces", "overall_score": 70}'
    assert extract_json(raw)["summary"] == "uses {curly} braces"


def test_escaped_quote_inside_a_string():
    raw = '{"summary": "he said \\"hi\\" loudly", "overall_score": 70}'
    assert extract_json(raw)["overall_score"] == 70


def test_first_object_wins_when_several_are_present():
    raw = '{"overall_score": 70} and then {"overall_score": 99}'
    assert extract_json(raw)["overall_score"] == 70


@pytest.mark.parametrize("raw", [
    "", "   ", "I cannot help with that request.",
    "Here is some prose with no JSON at all.",
    "{unclosed: ", "[1, 2, 3]",
])
def test_unsalvageable_input_raises(raw):
    with pytest.raises(ParseError):
        extract_json(raw)


def test_strip_fences_and_balanced_helpers():
    assert strip_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert extract_balanced_object("no braces here") is None
    assert extract_balanced_object('x {"a": {"b": 1}} y') == '{"a": {"b": 1}}'


# --------------------------------------------------------------------------- #
# contracts
# --------------------------------------------------------------------------- #
def test_scores_are_coerced_and_clamped():
    model = AnalystOutput.model_validate({
        "overall_score": "85",
        "score_breakdown": {"skills": 120, "experience": -5, "keywords": 70.6},
    })
    assert model.overall_score == 85
    assert model.score_breakdown.skills == 100
    assert model.score_breakdown.experience == 0
    assert model.score_breakdown.keywords == 71


def test_overall_score_derived_from_breakdown_when_omitted():
    """A confident 0 beside three healthy sub-scores would confuse the user."""
    model = AnalystOutput.model_validate(
        {"score_breakdown": {"skills": 60, "experience": 90, "keywords": 60}}
    )
    assert model.overall_score == 70


def test_skills_are_deduplicated_case_insensitively():
    model = AnalystOutput.model_validate(
        {"matched_skills": ["Python", "python", "PYTHON", "FastAPI"]}
    )
    assert model.matched_skills == ["Python", "FastAPI"]


def test_garbage_types_do_not_crash_validation():
    model = AnalystOutput.model_validate(
        {"overall_score": "high", "matched_skills": "not a list", "summary": None}
    )
    assert model.overall_score == 0
    assert model.matched_skills == []
    assert model.summary == ""


def test_bullets_capped_at_five_and_empties_dropped():
    model = RewriterOutput.model_validate({"rewritten_bullets": [
        {"original": f"o{i}", "improved": f"i{i}", "reason": "r"} for i in range(9)
    ]})
    assert len(model.rewritten_bullets) == 5

    model = RewriterOutput.model_validate({"rewritten_bullets": [
        {"original": "kept", "improved": "better", "reason": "r"},
        {"original": "no improvement", "improved": "", "reason": "r"},
    ]})
    assert len(model.rewritten_bullets) == 1


def test_wrong_shape_is_still_a_failure():
    """Valid JSON of the wrong type must not silently pass."""
    with pytest.raises(ValidationError):
        AnalystOutput.model_validate({"score_breakdown": "not an object"})
