"""Enumerations used across the data model.

Stored as strings (native_enum=False at the column) so adding a value is a
code change, not a Postgres enum-type migration.
"""

import enum


class RunStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"


class RunTrigger(enum.StrEnum):
    manual = "manual"
    scheduled = "scheduled"


class ConnectionSource(enum.StrEnum):
    search_console = "search_console"
    ga4 = "ga4"
    google_tag_manager = "google_tag_manager"
    ahrefs = "ahrefs"
    screaming_frog = "screaming_frog"


class ConnectionStatus(enum.StrEnum):
    connected = "connected"
    error = "error"
    disconnected = "disconnected"


class DetectionTag(enum.StrEnum):
    """How a finding was established. The spine of the graceful-degradation model."""

    observed = "observed"          # seen directly, or from a live connector
    inferred = "inferred"          # heuristic / LLM judgement, confidence-scored
    needs_connection = "needs_connection"  # would need a data source we don't have


class FindingStatus(enum.StrEnum):
    passed = "pass"
    warn = "warn"
    fail = "fail"
    info = "info"


class Severity(enum.StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"
