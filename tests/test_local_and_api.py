"""Local analyzer determinism, cache behaviour, and the API with no network."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import cache, db
from backend.agents.orchestrator import InputTooShort, analyze
from backend.fallback import local_analyzer
from backend.providers.base import AuthFailed
from backend.providers.router import CircuitBreaker, ProviderRouter

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
RESUME = (SAMPLES / "resume.txt").read_text(encoding="utf-8")
JOB = (SAMPLES / "job_description.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# local analyzer
# --------------------------------------------------------------------------- #
def test_local_analyzer_is_deterministic():
    assert local_analyzer.analyze(RESUME, JOB) == local_analyzer.analyze(RESUME, JOB)


def test_local_scores_are_in_range_and_shaped_right():
    out = local_analyzer.analyze(RESUME, JOB)
    assert 0 <= out["overall_score"] <= 100
    for value in out["score_breakdown"].values():
        assert 0 <= value <= 100
    assert out["rewritten_bullets"] == []
    assert len(out["suggestions"]) >= 3


def test_local_finds_the_real_gap():
    """The sample pair has a deliberate infrastructure gap."""
    out = local_analyzer.analyze(RESUME, JOB)
    assert "Python" in out["matched_skills"]
    assert "FastAPI" in out["matched_skills"]
    missing = " ".join(out["missing_skills"]).lower()
    assert "kubernetes" in missing or "terraform" in missing


def test_multiword_skills_are_detected_as_one_concept():
    found = local_analyzer.find_skills("We use infrastructure as code and CI/CD daily.")
    assert "infrastructure as code" in found
    assert "ci/cd" in found


def test_skill_matching_respects_token_boundaries():
    """'go' must not match inside 'going', and 'r' must not match everywhere."""
    assert "go" not in local_analyzer.find_skills("I am going to the shop")
    assert "go" in local_analyzer.find_skills("Built services in Go and Python")


def test_empty_input_does_not_divide_by_zero():
    out = local_analyzer.analyze("", "")
    assert out["overall_score"] == 0


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
def test_hash_ignores_whitespace_and_case():
    assert cache.input_hash("Python  FastAPI", "Need K8s") == \
           cache.input_hash("python\n\nfastapi", "need   k8s")


def test_hash_changes_when_content_changes():
    assert cache.input_hash("a" * 40, "b" * 40) != cache.input_hash("a" * 40, "c" * 40)


# --------------------------------------------------------------------------- #
# orchestrator degradation, no network
# --------------------------------------------------------------------------- #
def dead_router(settings, fake):
    """A router whose every provider fails with a terminal error."""
    providers = [fake(name, settings, responses=[AuthFailed("401")])
                 for name in ("alpha", "bravo")]
    return ProviderRouter(settings, providers=providers,
                          breaker=CircuitBreaker(9, 60), log_attempts=False)


def test_all_providers_down_degrades_to_local_fallback(settings, fake, memdb):
    result = analyze(RESUME, JOB, router=dead_router(settings, fake),
                     settings=settings, conn=memdb, force=True)

    assert result.source == "local_fallback"
    assert result.provider_used is None
    assert result.rewritten_bullets == []
    assert 0 <= result.score <= 100
    assert result.id is not None          # still persisted


def test_rewriter_failure_yields_a_partial_result(settings, fake, memdb):
    """Analyst succeeds, Rewriter cannot. The score must still be shown."""
    analyst_json = (
        '{"overall_score": 61, "score_breakdown": {"skills": 60, '
        '"experience": 62, "keywords": 61}, "matched_skills": ["Python"], '
        '"missing_skills": ["Kubernetes"], "summary": "ok"}'
    )
    # First two calls answer the Analyst (attempt 1 succeeds); everything
    # afterwards is the Rewriter, which only ever gets unparseable prose.
    provider = fake("alpha", settings,
                    responses=[analyst_json, "I cannot do that.", "Still no."])
    router = ProviderRouter(settings, providers=[provider],
                            breaker=CircuitBreaker(9, 60), log_attempts=False)

    result = analyze(RESUME, JOB, router=router, settings=settings,
                     conn=memdb, force=True)

    assert result.source == "ai"
    assert result.partial is True
    assert result.score == 61
    assert result.rewritten_bullets == []
    assert result.suggestions            # floor applied, never an empty heading


def test_cache_returns_the_same_row_without_calling_a_provider(settings, fake, memdb):
    analyst_json = (
        '{"overall_score": 55, "score_breakdown": {"skills": 55, '
        '"experience": 55, "keywords": 55}, "matched_skills": ["Python"], '
        '"missing_skills": ["AWS"], "summary": "ok"}'
    )
    rewriter_json = (
        '{"rewritten_bullets": [{"original": "Built APIs for internal tools '
        'used by the operations team", "improved": "Built and operated APIs", '
        '"reason": "clarity"}], "suggestions": ["Add AWS"]}'
    )
    provider = fake("alpha", settings, responses=[analyst_json, rewriter_json])
    router = ProviderRouter(settings, providers=[provider],
                            breaker=CircuitBreaker(9, 60), log_attempts=False)

    first = analyze(RESUME, JOB, router=router, settings=settings,
                    conn=memdb, force=True)
    calls_after_first = len(provider.calls)
    second = analyze(RESUME, JOB, router=router, settings=settings, conn=memdb)

    assert first.cached is False and second.cached is True
    assert second.id == first.id
    assert len(provider.calls) == calls_after_first    # no provider was called


def test_short_input_is_rejected_before_any_provider_call(settings, fake, memdb):
    provider = fake("alpha", settings)
    router = ProviderRouter(settings, providers=[provider], log_attempts=False)
    with pytest.raises(InputTooShort):
        analyze("too short", "also short", router=router, settings=settings,
                conn=memdb)
    assert provider.calls == []


# --------------------------------------------------------------------------- #
# API surface, no network
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(memdb, monkeypatch):
    from backend import main
    monkeypatch.setattr(main, "router",
                        ProviderRouter(main.settings, providers=[],
                                       log_attempts=False))
    return TestClient(main.app)


def test_health_reports_ok(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["db"] is True


def test_page_and_sample_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "ResumeMatch Coach" in page.text

    sample = client.get("/sample").json()
    assert "PRIYA" in sample["resume"] and "Senior Backend" in sample["job"]


@pytest.mark.parametrize("payload,fragment", [
    ({"resume_text": "  ", "job_text": JOB}, "Resume is empty"),
    ({"resume_text": RESUME, "job_text": ""}, "Job description is empty"),
    ({"resume_text": "x" * 15001, "job_text": JOB}, "limit is 15,000"),
])
def test_validation_rejects_bad_input(client, payload, fragment):
    response = client.post("/analyze", json=payload)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_analyze_with_no_providers_still_returns_a_result(client):
    """The headline promise: no provider, no error page."""
    response = client.post("/analyze", json={"resume_text": RESUME,
                                             "job_text": JOB, "force": True})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local_fallback"
    assert 0 <= body["score"] <= 100


def test_history_analysis_and_delete_round_trip(client):
    created = client.post("/analyze", json={"resume_text": RESUME,
                                            "job_text": JOB, "force": True}).json()
    analysis_id = created["id"]

    assert len(client.get("/history").json()) == 1
    assert client.get(f"/analysis/{analysis_id}").json()["score"] == created["score"]
    assert client.get("/analysis/999999").status_code == 404
    assert client.delete(f"/analysis/{analysis_id}").status_code == 204
    assert client.delete(f"/analysis/{analysis_id}").status_code == 404
    assert client.get("/history").json() == []


def test_compare_reports_the_delta_and_skill_diff(client):
    first = client.post("/analyze", json={"resume_text": RESUME,
                                          "job_text": JOB, "force": True}).json()
    better = RESUME.replace("Docker, Git", "Docker, Kubernetes, Terraform, AWS, Git")
    second = client.post("/analyze", json={"resume_text": better,
                                           "job_text": JOB}).json()

    body = client.get(f"/compare?a={first['id']}&b={second['id']}").json()
    assert body["score_delta"] == second["score"] - first["score"]
    assert "Kubernetes" in body["skills"]["newly_matched"]
    # Both analyses share one job row, so the trend has a point for each.
    assert len(body["trend"]) == 2

    assert client.get(f"/compare?a={first['id']}&b={first['id']}").status_code == 422
    assert client.get(f"/compare?a={first['id']}&b=999999").status_code == 404


def test_extract_reads_a_text_upload(client):
    response = client.post("/extract",
                           files={"file": ("cv.txt", RESUME.encode(), "text/plain")})
    body = response.json()
    assert response.status_code == 200
    assert "PRIYA" in body["text"] and body["pages"] is None

    bad = client.post("/extract",
                      files={"file": ("cv.exe", b"MZ\x00", "application/octet-stream")})
    assert bad.status_code == 422


def test_status_endpoint_leaks_nothing(client):
    text = client.get("/providers/status").text
    for secret_prefix in ("sk-or-", "nvapi-", "hf_", "cohere_"):
        assert secret_prefix not in text


# --------------------------------------------------------------------------- #
# tailored resume PDF
# --------------------------------------------------------------------------- #
from backend.resume_builder import apply_rewrites, build_pdf  # noqa: E402

BULLET = {
    "original": "Built APIs for internal tools used by the operations team",
    "improved": "Built and operated Python REST APIs, owning delivery end to end",
    "reason": "ownership",
}


def test_rewrite_replaces_the_line_and_keeps_its_marker():
    report = apply_rewrites(RESUME, [BULLET])
    assert report.applied == 1
    assert report.skipped == []
    assert f"- {BULLET['improved']}" in report.text
    assert BULLET["original"] not in report.text


def test_rewrite_leaves_every_other_line_untouched():
    """It is the user's resume being edited, not regenerated."""
    report = apply_rewrites(RESUME, [BULLET])
    before, after = RESUME.splitlines(), report.text.splitlines()
    assert len(before) == len(after)
    assert sum(1 for a, b in zip(before, after) if a != b) == 1


def test_rewrite_matches_a_slightly_reworded_original():
    """Models echo the bullet approximately; pass 2 catches that."""
    report = apply_rewrites(RESUME, [{
        "original": "Containerised three services with Docker",   # resume says "... for local development"
        "improved": "Containerised three services with Docker, standardising local dev",
        "reason": "impact",
    }])
    assert report.applied == 1


def test_unmatchable_rewrite_is_reported_not_silently_dropped():
    report = apply_rewrites(RESUME, [{
        "original": "A line that does not exist in this resume",
        "improved": "whatever", "reason": "n/a",
    }])
    assert report.applied == 0
    assert len(report.skipped) == 1
    assert report.text == RESUME


def test_pdf_is_a_real_parseable_document():
    from pypdf import PdfReader
    import io

    report = apply_rewrites(RESUME, [BULLET])
    pdf = build_pdf(report.text)
    assert pdf.startswith(b"%PDF-")

    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "PRIYA RAGHAVAN" in text        # name survived
    assert "EXPERIENCE" in text            # headings survived
    assert "owning delivery end to end" in text   # the rewrite is in there


def test_pdf_handles_characters_the_core_fonts_cannot_encode():
    """Smart quotes and en-dashes must degrade, never raise."""
    pdf = build_pdf("Jo\u2019s Resume\n\nEXPERIENCE\n- Cut p95 \u2013 latency by 38%\u2026")
    assert pdf.startswith(b"%PDF-")


def test_pdf_endpoint_returns_an_attachment(client, monkeypatch):
    from backend import main

    created = client.post("/analyze", json={"resume_text": RESUME,
                                            "job_text": JOB, "force": True}).json()
    # The local fallback produces no bullets, so inject one the way the
    # Rewriter would have, and confirm the endpoint applies it.
    monkeypatch.setattr(main.db, "get_analysis",
                        lambda i, conn=None, owner=None: {**created, "rewritten_bullets": [BULLET]})

    res = client.get(f"/analysis/{created['id']}/resume.pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]
    assert res.headers["x-rewrites-applied"] == "1"
    assert res.content.startswith(b"%PDF-")


def test_pdf_endpoint_refuses_when_nothing_would_change(client):
    """A local-fallback result has no rewrites, so there is no tailored resume."""
    created = client.post("/analyze", json={"resume_text": RESUME,
                                            "job_text": JOB, "force": True}).json()
    assert created["rewritten_bullets"] == []

    res = client.get(f"/analysis/{created['id']}/resume.pdf")
    assert res.status_code == 422
    assert "no rewritten bullets" in res.json()["detail"]


def test_pdf_endpoint_404s_for_a_missing_analysis(client):
    assert client.get("/analysis/999999/resume.pdf").status_code == 404


def test_thin_suggestion_lists_are_topped_up(settings, fake, memdb):
    """A model that returns one suggestion must not render a one-line card."""
    analyst_json = (
        '{"overall_score": 42, "score_breakdown": {"skills": 43, '
        '"experience": 35, "keywords": 50}, "matched_skills": ["React"], '
        '"missing_skills": ["Storybook", "Playwright"], "summary": "ok"}'
    )
    rewriter_json = (
        '{"rewritten_bullets": [{"original": "Built APIs for internal tools '
        'used by the operations team", "improved": "Built and ran APIs", '
        '"reason": "ownership"}], "suggestions": ["Add Storybook docs"]}'
    )
    provider = fake("alpha", settings, responses=[analyst_json, rewriter_json])
    router = ProviderRouter(settings, providers=[provider],
                            breaker=CircuitBreaker(9, 60), log_attempts=False)

    result = analyze(RESUME, JOB, router=router, settings=settings,
                     conn=memdb, force=True)

    assert len(result.suggestions) >= 3
    assert result.suggestions[0] == "Add Storybook docs"      # the model's wins
    assert any("Storybook" in s for s in result.suggestions[1:])  # gap-derived
    assert len(result.suggestions) == len(set(result.suggestions))  # no repeats


def test_ai_results_never_claim_the_ai_was_offline(settings, fake, memdb):
    """The top-up advice must not include the local-fallback-only line."""
    analyst_json = (
        '{"overall_score": 42, "score_breakdown": {"skills": 43, '
        '"experience": 35, "keywords": 50}, "matched_skills": ["React"], '
        '"missing_skills": ["Storybook"], "summary": "ok"}'
    )
    rewriter_json = (
        '{"rewritten_bullets": [{"original": "Built APIs for internal tools '
        'used by the operations team", "improved": "Built and ran APIs", '
        '"reason": "ownership"}], "suggestions": ["Add Storybook docs"]}'
    )
    provider = fake("alpha", settings, responses=[analyst_json, rewriter_json])
    router = ProviderRouter(settings, providers=[provider],
                            breaker=CircuitBreaker(9, 60), log_attempts=False)

    result = analyze(RESUME, JOB, router=router, settings=settings,
                     conn=memdb, force=True)

    assert result.source == "ai"
    assert not any("provider is available" in s for s in result.suggestions)

    # ...but the genuinely offline path should still say it.
    offline = local_analyzer.analyze(RESUME, JOB)
    assert any("provider is available" in s for s in offline["suggestions"])
