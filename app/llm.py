"""Thin wrapper around the Anthropic SDK for the LLM judgement passes.

One place that knows how to call Claude with a structured-output schema. Every
caller is an audit check, so the contract is: never raise into an audit. When
there's no API key, or the call fails, or the response can't be parsed, this
returns None and the caller degrades to a needs-connection finding. That keeps
the outside-in audit working with or without the key.

Uses messages.parse so the response is validated against a Pydantic schema; the
parsed model is returned via parsed_output.
"""

from __future__ import annotations

import logging
import re

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_WS_RE = re.compile(r"\s+")


def visible_text(html: str, max_chars: int = 6000) -> str:
    """Reduce an HTML document to its visible text, capped for prompt size."""
    without_code = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", without_code)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]


def available() -> bool:
    """True when an API key is configured, so a check can decide before building a prompt."""
    return bool(settings.anthropic_api_key)


def judge[T: BaseModel](
    system: str, user: str, schema: type[T], *, max_tokens: int = 1024
) -> T | None:
    """Run one structured judgement. Returns the parsed model, or None on any failure.

    The None path is deliberate: callers treat a missing result as "could not assess
    with the LLM" and fall back to their non-LLM finding.
    """
    if not settings.anthropic_api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.parse(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
    except (anthropic.AnthropicError, ValidationError) as exc:
        logger.warning("LLM judgement failed: %s: %s", type(exc).__name__, exc)
        return None

    parsed = message.content[-1].parsed_output if message.content else None
    if not isinstance(parsed, schema):
        logger.warning("LLM judgement returned no parsable output")
        return None
    return parsed
