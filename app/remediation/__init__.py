"""Remediation catalogue and the prioritised action list.

The catalogue (catalogue.py) maps every check we run to a commercial impact, an
effort to fix, and plain-English why-and-how text. The ranking (ranking.py) turns
a run's live findings into a deterministic, impact-first action list. Together they
are the output layer the dashboard and report lead with.
"""
