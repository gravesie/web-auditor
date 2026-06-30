"""The shape of a remediation catalogue entry, and the impact / effort scales.

Two ratings drive the prioritised action list. Both are small integers so the
ranking sort is unambiguous and reproducible.

Commercial impact, 1 to 5, is how much a finding bears on traffic, leads or
conversion right now:

    5  directly loses traffic or leads today (site not indexable, no conversion
       path, blocked crawlers, broken HTTPS)
    4  strong drag (missing titles, slow LCP, striking-distance not captured,
       trackers firing before consent)
    3  moderate (thin content, weak headline, missing canonical)
    2  minor or hygiene (missing alt text, stale third-party inventory)
    1  marginal, often informational

Effort to fix, 1 to 3, is the work the fix takes:

    1  quick win: a config or copy change, minutes to an hour
    2  moderate: a few hours, usually a developer or content task
    3  project: sustained content or engineering work

The why and how are the single source of recommendation wording: why it matters in
commercial terms, and how to fix it, each one sentence.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_IMPACT, MAX_IMPACT = 1, 5
MIN_EFFORT, MAX_EFFORT = 1, 3


@dataclass(frozen=True)
class RemediationEntry:
    impact: int
    effort: int
    why: str
    how: str

    def __post_init__(self) -> None:
        if not MIN_IMPACT <= self.impact <= MAX_IMPACT:
            raise ValueError(f"impact must be {MIN_IMPACT}..{MAX_IMPACT}, got {self.impact}")
        if not MIN_EFFORT <= self.effort <= MAX_EFFORT:
            raise ValueError(f"effort must be {MIN_EFFORT}..{MAX_EFFORT}, got {self.effort}")
        if not self.why.strip():
            raise ValueError("why must not be empty")
        if not self.how.strip():
            raise ValueError("how must not be empty")
