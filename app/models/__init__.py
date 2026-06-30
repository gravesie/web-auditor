"""Model package. Importing it registers every table on Base.metadata."""

from app.models.account import Account, User
from app.models.base import Base
from app.models.run import AuditRun, Finding, Page, Query, SubAuditResult
from app.models.site import Connection, Site

__all__ = [
    "Base",
    "Account",
    "User",
    "Site",
    "Connection",
    "AuditRun",
    "SubAuditResult",
    "Page",
    "Query",
    "Finding",
]
