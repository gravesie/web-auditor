"""The page-one / dashboard-home presentation model.

Rolls the eleven technical sub-audits up into the commercial funnel a business owner
actually thinks in: can customers get found, do they win trust, do visitors convert.
Analytics ("can you even measure it") and the build/compliance foundations sit as
quiet secondary strips. The eleven audits are unchanged underneath; this is a
commercial lens over them, driven by the same commercially weighted scores.

Both the web dashboard home and page one of the PDF read from this one model, so the
two surfaces can't drift. The narrative fields (verdict, pillar reads) carry a plain
deterministic default here; an LLM pass can enrich the wording later without ever
changing the numbers.

The pillar mapping is deliberately a single config block (PILLARS / MEASURE /
FOUNDATIONS). It maps at the audit level for now, which is accurate enough and needs
no schema change; it can move to category level later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditRun, SubAuditResult
from app.remediation.ranking import ActionItem
from app.reporting.view import build_action_list
from app.weighting import weight_for

# --- Config: the commercial funnel and the two secondary strips ---
# audit_key -> pillar. Each audit sits in exactly one place.
PILLARS: list[tuple[str, str, list[str]]] = [
    ("get_found", "Get found", ["technical_seo", "on_page_seo", "schema", "geo", "performance"]),
    ("win_trust", "Win trust", ["content_quality", "content_strategy"]),
    ("convert", "Convert", ["messaging"]),
]
MEASURE = ("measure", "Can you measure it?", ["analytics"])
FOUNDATIONS = ("foundations", "Foundations", ["build_security", "compliance"])

# Top fixes to surface on page one; the rest live in the full action list.
_TOP_FIXES = 5
_MAX_STRENGTHS = 3

# Audits in the quiet Foundations strip. Their fixes are demoted below the commercial
# ones on page one, honouring the brief that traffic, content and conversion matter
# more to the owner than compliance and hardening. Genuinely severe foundations issues
# (a real breach, an exposed secret) still surface, per the spec's rule that severe
# findings are flagged regardless of weight.
_FOUNDATIONS_AUDITS = {"build_security", "compliance"}
_SEVERE = {"critical", "high"}


def _demote_from_page_one(item: ActionItem) -> bool:
    """True when a fix is foundations work that shouldn't lead the owner's page.

    Compliance and GDPR are always demoted: they're a risk matter, not a growth lever,
    and the brief is explicit that they matter less to the owner than traffic, content
    and conversion. Security is demoted too, except when severe (an exposed secret, a
    broken certificate), because those are existential and commercial, not box-ticking.
    """
    if item.audit_key == "compliance":
        return True
    if item.audit_key == "build_security":
        return item.severity not in _SEVERE
    return False

# Score bands. Grade for the headline, status for the traffic-light on each pillar.
_GRADE_BANDS = [(85, "A"), (70, "B"), (55, "C"), (40, "D"), (25, "E")]
_GOOD, _WATCH = 70.0, 50.0  # >= good, >= watch, else problem

# Positive one-liners used when an audit scores well, for the "what's working" list.
_STRENGTH_PHRASES = {
    "technical_seo": "Sound technical SEO foundations.",
    "performance": "Fast, responsive pages.",
    "build_security": "Secure and well built.",
    "messaging": "Clear, compelling messaging.",
    "content_quality": "Credible, substantial content.",
    "schema": "Strong structured-data markup.",
    "on_page_seo": "Well-optimised pages.",
    "geo": "Visible to AI search.",
    "content_strategy": "An active content engine.",
    "analytics": "Good measurement in place.",
    "compliance": "Compliant and low risk.",
}


@dataclass
class PillarView:
    key: str
    label: str
    score: float | None
    status: str  # good | watch | problem | none
    read: str


@dataclass
class MiniView:
    key: str
    label: str
    status: str
    detail: str


@dataclass
class PageOneView:
    score: float | None
    grade: str
    status_label: str
    verdict: str
    pillars: list[PillarView]
    measure: MiniView
    foundations: MiniView
    top_fixes: list[ActionItem]
    strengths: list[str] = field(default_factory=list)


def _grade(score: float | None) -> str:
    if score is None:
        return "n/a"
    for threshold, letter in _GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def _status(score: float | None) -> str:
    if score is None:
        return "none"
    if score >= _GOOD:
        return "good"
    if score >= _WATCH:
        return "watch"
    return "problem"


def _status_label(score: float | None) -> str:
    grade = _grade(score)
    return {
        "A": "On track",
        "B": "On track",
        "C": "Below target",
        "D": "Needs work",
        "E": "Needs work",
        "F": "At risk",
        "n/a": "Not yet assessed",
    }[grade]


def _pillar_score(members: list[str], audit_scores: dict[str, float | None]) -> float | None:
    """Commercially weighted mean of a pillar's member audits that have a score."""
    scored = [(k, audit_scores[k]) for k in members if audit_scores.get(k) is not None]
    total_weight = sum(weight_for(k) for k, _ in scored)
    if not total_weight:
        return None
    return sum(score * weight_for(k) for k, score in scored) / total_weight


def _pillar_read(status: str) -> str:
    return {
        "good": "A strength to build on.",
        "watch": "Solid, with clear room to improve.",
        "problem": "A weak point costing you results.",
        "none": "Not enough data to assess yet.",
    }[status]


def _verdict(score: float | None, pillars: list[PillarView]) -> str:
    """A plain default verdict; an LLM pass can replace the wording later."""
    if score is None:
        return "Not enough data to assess this site yet."
    scored = [p for p in pillars if p.score is not None]
    if not scored:
        return "Not enough data to assess this site yet."
    strongest = max(scored, key=lambda p: p.score)
    weakest = min(scored, key=lambda p: p.score)
    if weakest.status == "problem":
        return (
            f"{strongest.label} is working, but {weakest.label.lower()} is the weak link "
            f"and where the money is."
        )
    if score >= _GOOD:
        return f"A strong site overall, led by {strongest.label.lower()}."
    return f"A reasonable base, with {weakest.label.lower()} the clearest opportunity."


def _strengths(audit_scores: dict[str, float | None]) -> list[str]:
    strong = sorted(
        ((k, v) for k, v in audit_scores.items() if v is not None and v >= _GOOD),
        key=lambda kv: kv[1],
        reverse=True,
    )
    phrases = [_STRENGTH_PHRASES[k] for k, _ in strong if k in _STRENGTH_PHRASES]
    return phrases[:_MAX_STRENGTHS]


def _mini(config: tuple[str, str, list[str]], audit_scores: dict[str, float | None]) -> MiniView:
    key, label, members = config
    score = _pillar_score(members, audit_scores)
    status = _status(score)
    detail = {
        "good": "In good shape.",
        "watch": "Worth a look.",
        "problem": "Needs attention.",
        "none": "Not assessed.",
    }[status]
    return MiniView(key=key, label=label, status=status, detail=detail)


def assemble_page_one(
    audit_scores: dict[str, float | None],
    site_score: float | None,
    action_items: list[ActionItem],
) -> PageOneView:
    """Pure assembly of the page-one model from scores and the ranked action list."""
    # Commercial-first ordering for the owner's "start here": a stable sort that keeps
    # the deterministic impact/effort order but sinks routine foundations work.
    page_fixes = sorted(action_items, key=_demote_from_page_one)

    pillars: list[PillarView] = []
    for key, label, members in PILLARS:
        score = _pillar_score(members, audit_scores)
        status = _status(score)
        pillars.append(
            PillarView(key=key, label=label, score=score, status=status, read=_pillar_read(status))
        )

    return PageOneView(
        score=site_score,
        grade=_grade(site_score),
        status_label=_status_label(site_score),
        verdict=_verdict(site_score, pillars),
        pillars=pillars,
        measure=_mini(MEASURE, audit_scores),
        foundations=_mini(FOUNDATIONS, audit_scores),
        top_fixes=page_fixes[:_TOP_FIXES],
        strengths=_strengths(audit_scores),
    )


def build_page_one(session: Session, run: AuditRun) -> PageOneView:
    """DB-backed wrapper: gather a run's audit scores and action list, then assemble."""
    results = session.execute(
        select(SubAuditResult).where(SubAuditResult.run_id == run.id)
    ).scalars().all()
    audit_scores = {sar.audit_key: sar.score for sar in results}
    action_items = build_action_list(session, run)
    return assemble_page_one(audit_scores, run.site_score, action_items)
