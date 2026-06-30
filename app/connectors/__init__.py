"""Live data-source connectors (Google Search Console, GA4).

Each connector turns a stored, encrypted OAuth grant into the data an audit needs,
and degrades gracefully: any failure marks the connection in error and leaves the
audit on its public/inferred path rather than breaking the run.
"""
