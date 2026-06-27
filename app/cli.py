"""Command-line entry point: run an audit and print the result.

Usage: python -m app.cli example.com
"""

from __future__ import annotations

import argparse

from app.runner import run_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a website audit and store the result.")
    parser.add_argument("domain", help="Domain or URL to audit, e.g. example.com")
    args = parser.parse_args()

    summary = run_audit(args.domain)

    print(
        f"\nAudit: {summary['domain']}  ->  {summary['final_url']}  "
        f"(HTTP {summary['status_code']})"
    )
    if summary["error"]:
        print(f"  acquisition error: {summary['error']}")

    print(f"Pages crawled: {summary['page_count']}")
    score = summary["site_score"]
    print(f"Site score: {score:.1f}/100\n" if score is not None else "Site score: n/a\n")

    for audit in summary["audits"]:
        print(
            f"[{audit['key']}] score {audit['score']:.1f}"
            f"  completeness {audit['completeness'] * 100:.0f}%"
        )
        for cat in audit["categories"]:
            if cat["applicable"] and cat["score"] is not None:
                print(f"  {cat['key']}: {cat['score']:.0f}")
            else:
                print(f"  {cat['key']}: --  (not yet assessed)")
            for ch in cat["checks"]:
                marker = "   " if ch["score"] is None else f"{ch['score']:>3.0f}"
                print(f"      {marker}  {ch['status']:<5} {ch['key']}: {ch['value']}")
    print()


if __name__ == "__main__":
    main()
