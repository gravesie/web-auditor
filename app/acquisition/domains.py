"""Registrable-domain helper, shared by the crawler and the renderer.

Uses a small known multi-label suffix list rather than the full Public Suffix
List. Good enough for a first cut (UK-heavy by design); replace with the PSL when
accuracy demands it.
"""

from __future__ import annotations

_SECOND_LEVEL = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "sch.uk",
    "com.au", "net.au", "org.au", "co.nz", "co.za", "com.br", "co.jp", "co.in", "co.kr",
}


def registrable_domain(host: str) -> str:
    host = (host or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])
