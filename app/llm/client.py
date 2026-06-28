"""Shared Claude client for the LLM judgement pass.

A single typed entry point, assess(), that several audits use to score the checks
that genuinely need a model (content depth and originality, message clarity, and
so on). It uses the official Anthropic SDK with structured outputs, so callers get
a validated object back.

Graceful by design: with no API key configured, or on any API error, assess()
returns None and the calling check falls back to its rule-based / needs-LLM
behaviour. The LLM pass must never break an audit run.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel

from app.config import settings

_client: anthropic.Anthropic | None = None


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def assess[T: BaseModel](
    system: str, content: str, schema: type[T], max_tokens: int = 1024
) -> T | None:
    """Run one structured judgement call. Returns a validated schema instance, or None.

    None means the pass is unavailable (no key) or the call failed; callers must
    degrade rather than treat None as a result.
    """
    if not is_configured():
        return None
    try:
        response = _get_client().messages.parse(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
        return response.parsed_output
    except Exception:  # noqa: BLE001 -- enrichment must never break an audit run
        return None
