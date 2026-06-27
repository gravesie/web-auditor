# Web Auditor

A website audit suite. It runs nine sub-audits against a site, scores each one,
and rolls them into a single site score. Every audit runs outside-in with no
client data, and goes deeper when a data source (Search Console, GA4, Ahrefs,
Screaming Frog) is connected.

The audit definitions, scoring and the system design live in the specification
and architecture documents (kept outside this repo, in the project folder).

## Stack

- FastAPI (Python) API and server-rendered front end (Jinja2 + HTMX)
- PostgreSQL, via Docker
- SQLAlchemy 2.0 and Alembic migrations
- Background workers for the audit runs (added with the acquisition stage)

## Prerequisites

- Python 3.13
- Docker Desktop (for Postgres)

## Setup

```sh
# 1. Virtual environment
py -3.13 -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1

# 2. Dependencies
pip install -e ".[dev]"

# 3. Environment file
copy .env.example .env          # then edit if needed

# 4. Database
docker compose up -d db

# 5. Schema
alembic upgrade head

# 6. Run
uvicorn app.main:app --reload
```

Then open http://localhost:8000 for the dashboard and
http://localhost:8000/health for the database check.

## Project layout

```
app/
  config.py        settings (env / .env)
  db.py            engine and session
  models/          SQLAlchemy models (site, connection, run, result, finding, page, query)
  audits/          sub-audit modules; base.py is the interface each implements
  web/templates/   Jinja2 templates
  main.py          FastAPI app
alembic/           migrations
docker-compose.yml Postgres
```

## Migrations

```sh
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```
