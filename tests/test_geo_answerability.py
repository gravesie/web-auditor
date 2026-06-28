"""GEO answerability: the LLM pass and its graceful fallback without a key."""

from app import llm
from app.audits import geo
from app.audits.geo import (
    GeoAudit,
    _AnswerabilityJudgement,
    _AnsweredQuestion,
    _llm_answerability,
)


def test_visible_text_strips_tags_and_scripts():
    html = (
        "<html><head><style>.x{}</style></head><body>"
        "<h1>Hi</h1><script>bad()</script><p>Real text</p></body></html>"
    )
    text = llm.visible_text(html)
    assert "Hi" in text and "Real text" in text
    assert "bad()" not in text and ".x{}" not in text


def test_llm_answerability_none_without_text():
    assert _llm_answerability("") is None


def test_llm_answerability_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    assert _llm_answerability("some page text") is None


def _fake_judgement(answered_count: int, total: int) -> _AnswerabilityJudgement:
    questions = [
        _AnsweredQuestion(question=f"Q{i}", answered=(i < answered_count), note="n")
        for i in range(total)
    ]
    return _AnswerabilityJudgement(questions=questions, summary="ok")


def test_llm_answerability_scores_and_lists_gaps(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(geo.llm, "judge", lambda *a, **k: _fake_judgement(3, 4))
    result = _llm_answerability("page text")
    assert result.score == 75.0
    assert result.detection.value == "inferred"
    assert result.evidence["unanswered"] == ["Q3"]
    assert "Q3" in result.recommendation


def test_llm_answerability_low_score_is_high_severity(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(geo.llm, "judge", lambda *a, **k: _fake_judgement(1, 5))
    result = _llm_answerability("page text")
    assert result.score == 20.0
    assert result.severity.value == "high"


def test_answerability_category_falls_back_without_key(monkeypatch):
    monkeypatch.setattr(geo.llm, "available", lambda: False)
    cat = GeoAudit()._answerability("<p>no faq here</p>")
    keys = {c.key for c in cat.checks}
    assert keys == {"faq_content", "icp_question_coverage"}
    coverage = next(c for c in cat.checks if c.key == "icp_question_coverage")
    assert coverage.detection.value == "needs_connection"


def test_answerability_category_uses_llm_when_available(monkeypatch):
    monkeypatch.setattr(geo.llm, "available", lambda: True)
    monkeypatch.setattr(geo.llm, "judge", lambda *a, **k: _fake_judgement(4, 5))
    cat = GeoAudit()._answerability("<p>frequently asked questions</p>")
    keys = {c.key for c in cat.checks}
    assert keys == {"faq_content", "icp_question_coverage", "icp_question_source"}
    coverage = next(c for c in cat.checks if c.key == "icp_question_coverage")
    assert coverage.detection.value == "inferred"
    assert coverage.score == 80.0
