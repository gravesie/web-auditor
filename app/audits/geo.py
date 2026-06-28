"""GEO / AI search visibility audit (spec 13.8).

The youngest field, so the biggest honesty caveat: GEO practice is not settled and
AI answers are non-deterministic. This separates the outcome (are you cited) from
the readiness (are you built to be cited).

The outcome, AI-answer citation, needs live queries to ChatGPT, Perplexity and AI
Overviews, which is an integration we have not built; it reports as needs-connection
rather than a guess. The readiness side, AI-crawler access and extractable
structure, is assessed rule-based here.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from app import llm, scoring
from app.acquisition.fetcher import Acquisition
from app.acquisition.render import RenderResult
from app.audits.base import (
    AuditContext,
    AuditModule,
    AuditResult,
    CategoryDef,
    CategoryResult,
    CheckResult,
)
from app.models.enums import DetectionTag, FindingStatus, Severity

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

USER_AGENT = "WebAuditor/0.1 (+https://github.com/gravesie/web-auditor)"

CATEGORIES = [
    CategoryDef("ai_citation", "AI answer presence and citation", 30),
    CategoryDef("extractability", "Extractability and structure", 25),
    CategoryDef("answerability", "Answerability of ICP questions", 20),
    CategoryDef("ai_crawler_access", "AI crawler access", 15),
    CategoryDef("authority", "Authority and corroboration", 10),
]
_DEFS = {c.key: c for c in CATEGORIES}

# AI crawlers commonly named in robots.txt.
_AI_BOTS = [
    "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "anthropic-ai",
    "Claude-Web", "PerplexityBot", "Google-Extended", "CCBot", "Applebot-Extended",
    "Bytespider", "Amazonbot", "meta-externalagent",
]
_HEADING_RE = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_LI_RE = re.compile(r"<li\b", re.I)
_TABLE_RE = re.compile(r"<table\b", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_AUTHORITY_RE = re.compile(r"wikipedia\.org|crunchbase\.com|linkedin\.com/company", re.I)


def _ai_crawler_blocks(robots_txt: str) -> set[str]:
    """AI bots that robots.txt disallows site-wide."""
    wanted = {b.lower() for b in _AI_BOTS}
    blocked: set[str] = set()
    current: set[str] = set()
    seen_rule = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            current, seen_rule = set(), False
            continue
        if ":" not in line:
            continue
        field, value = (p.strip() for p in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if seen_rule:
                current, seen_rule = set(), False
            current.add(value.lower())
        elif field == "disallow":
            seen_rule = True
            if value == "/":
                blocked |= current & wanted
    return blocked


class _AnsweredQuestion(BaseModel):
    question: str
    answered: bool
    note: str


class _AnswerabilityJudgement(BaseModel):
    questions: list[_AnsweredQuestion]
    summary: str


_ANSWERABILITY_SYSTEM = (
    "You assess whether a web page answers the questions a prospective buyer would ask "
    "before choosing this provider. Infer 5 to 7 realistic buyer questions from the page "
    "(pricing, what is offered, who it is for, proof, how to start, and similar), then "
    "judge whether the page actually answers each. Mark answered=true only when the page "
    "gives a direct, findable answer, not merely a related mention. Keep each note to one "
    "short sentence."
)


def _llm_answerability(text: str) -> CheckResult | None:
    """LLM judgement of how well the page answers likely buyer questions. None if unavailable."""
    if not text or not llm.available():
        return None
    result = llm.judge(
        _ANSWERABILITY_SYSTEM,
        f"Page content:\n\n{text}",
        _AnswerabilityJudgement,
        max_tokens=1500,
    )
    if result is None or not result.questions:
        return None

    answered = [q for q in result.questions if q.answered]
    unanswered = [q for q in result.questions if not q.answered]
    score = len(answered) / len(result.questions) * 100
    if score >= 80:
        status, severity = FindingStatus.passed, Severity.low
    elif score >= 50:
        status, severity = FindingStatus.warn, Severity.medium
    else:
        status, severity = FindingStatus.warn, Severity.high

    recommendation = None
    if unanswered:
        gaps = "; ".join(q.question for q in unanswered[:5])
        recommendation = f"Answer these buyer questions directly on the page: {gaps}"

    return CheckResult(
        "icp_question_coverage",
        score,
        status,
        severity,
        _INF,
        value=(
            f"answers {len(answered)} of {len(result.questions)} likely buyer questions: "
            f"{result.summary}"
        ),
        recommendation=recommendation,
        evidence={
            "answered": [q.question for q in answered],
            "unanswered": [q.question for q in unanswered],
        },
    )


def _has_llms_txt(host: str) -> bool:
    try:
        resp = httpx.get(
            f"https://{host}/llms.txt",
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except httpx.HTTPError:
        return False
    return resp.status_code == 200 and "text" in resp.headers.get("content-type", "")


class GeoAudit(AuditModule):
    key = "geo"
    label = "GEO / AI search visibility"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        dom = render.html or acq.html
        host = urlsplit(render.final_url or acq.final_url or f"https://{context.site_domain}").hostname

        cats = [
            self._ai_citation(),
            self._extractability(dom),
            self._answerability(dom),
            self._ai_crawler_access(acq, host or ""),
            self._authority(dom),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _ai_citation(self) -> CategoryResult:
        note = CheckResult(
            "ai_answer_citation", None, FindingStatus.info, Severity.info, _NEEDS,
            value=(
                "whether AI engines cite this site needs live queries to "
                "ChatGPT / Perplexity / AI Overviews (integration pending)"
            ),
        )
        return CategoryResult("ai_citation", None, True, [note])

    def _extractability(self, dom: str) -> CategoryResult:
        headings = [_TAG_RE.sub(" ", h).strip() for h in _HEADING_RE.findall(dom)]
        questions = [h for h in headings if h.endswith("?")]
        has_faq_schema = "faqpage" in dom.lower()
        qa = bool(questions) or has_faq_schema
        qa_check = CheckResult(
            "qa_structure",
            100.0 if qa else 50.0,
            FindingStatus.passed if qa else FindingStatus.warn,
            Severity.low,
            _INF,
            value=(
                f"{len(questions)} question-style heading(s)"
                + (" + FAQ schema" if has_faq_schema else "")
                if qa
                else "no question-and-answer structure for extraction"
            ),
            recommendation=None if qa else "Structure content as clear questions and answers.",
        )

        chunks = len(_LI_RE.findall(dom)) >= 3 or bool(_TABLE_RE.search(dom))
        chunk_check = CheckResult(
            "extractable_chunks",
            100.0 if chunks else 60.0,
            FindingStatus.passed if chunks else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=(
                "lists/tables present for easy extraction"
                if chunks
                else "little list/table structure"
            ),
            recommendation=None if chunks else "Use lists and tables for facts AI can lift.",
        )
        checks = [qa_check, chunk_check]
        return CategoryResult("extractability", scoring.category_score(checks), True, checks)

    def _answerability(self, dom: str) -> CategoryResult:
        low = dom.lower()
        has_qa = "frequently asked" in low or "faqpage" in low
        faq = CheckResult(
            "faq_content",
            100.0 if has_qa else 60.0,
            FindingStatus.passed if has_qa else FindingStatus.warn,
            Severity.low,
            _INF,
            value="FAQ / Q&A content present" if has_qa else "no explicit FAQ / Q&A content",
            recommendation=None if has_qa else "Answer common questions directly.",
        )

        # LLM judgement of buyer-question coverage when the key is configured;
        # otherwise the original needs-connection note so the audit still runs.
        coverage = _llm_answerability(llm.visible_text(dom))
        if coverage is None:
            coverage = CheckResult(
                "icp_question_coverage", None, FindingStatus.info, Severity.info, _NEEDS,
                value="buyer-question coverage needs an LLM pass (no API key configured)",
            )
            checks = [faq, coverage]
        else:
            # The inferred coverage is from buyer questions Claude infers from the page;
            # the data-backed version (real questions from GA4 / Search Console) is deeper.
            upgrade = CheckResult(
                "icp_question_source", None, FindingStatus.info, Severity.info, _NEEDS,
                value="real buyer questions from Search Console / GA4 would sharpen this",
            )
            checks = [faq, coverage, upgrade]
        return CategoryResult("answerability", scoring.category_score(checks), True, checks)

    def _ai_crawler_access(self, acq: Acquisition, host: str) -> CategoryResult:
        if acq.robots_txt is None:
            access = CheckResult(
                "ai_crawler_access", 100.0, FindingStatus.passed, Severity.low, _OBS,
                value="no robots.txt restrictions on AI crawlers",
            )
        else:
            blocked = _ai_crawler_blocks(acq.robots_txt)
            if blocked:
                access = CheckResult(
                    "ai_crawler_access", 50.0, FindingStatus.warn, Severity.medium, _OBS,
                    value="robots.txt blocks AI crawlers: " + ", ".join(sorted(blocked)),
                    recommendation=(
                        "If you want AI visibility, allow these crawlers; "
                        "blocking is valid only if deliberate."
                    ),
                    evidence={"blocked": sorted(blocked)},
                )
            else:
                access = CheckResult(
                    "ai_crawler_access", 100.0, FindingStatus.passed, Severity.low, _OBS,
                    value="AI crawlers are not blocked",
                )

        has_llms = _has_llms_txt(host) if host else False
        llms = CheckResult(
            "llms_txt",
            100.0 if has_llms else 80.0,
            FindingStatus.passed if has_llms else FindingStatus.info,
            Severity.info,
            _OBS,
            value="llms.txt present" if has_llms else "no llms.txt (emerging, optional)",
        )
        checks = [access, llms]
        return CategoryResult("ai_crawler_access", scoring.category_score(checks), True, checks)

    def _authority(self, dom: str) -> CategoryResult:
        signals = bool(_AUTHORITY_RE.search(dom)) or "sameas" in dom.lower()
        check = CheckResult(
            "corroboration_signals",
            100.0 if signals else 60.0,
            FindingStatus.passed if signals else FindingStatus.warn,
            Severity.low,
            _INF,
            value=(
                "links to authoritative profiles / sameAs entity links present"
                if signals
                else "few external corroboration signals"
            ),
            recommendation=(
                None if signals else "Build and link authoritative profiles (entity corroboration)."
            ),
        )
        note = CheckResult(
            "off_page_authority", None, FindingStatus.info, Severity.info, _NEEDS,
            value="full corroboration across the web needs the staged off-page authority audit",
        )
        checks = [check, note]
        return CategoryResult("authority", scoring.category_score(checks), True, checks)
