# Fundi Notes

[![Tests](https://github.com/Kare100/fundi-notes/actions/workflows/tests.yml/badge.svg)](https://github.com/Kare100/fundi-notes/actions/workflows/tests.yml)

Turns field technicians' voice notes into structured job reports for small service businesses.

A manager uploads a recording, the app transcribes it, extracts the job details, flags what is missing or
contradictory, writes a short report, and shows everything on a dashboard with a daily executive summary.

* **Backend:** Python 3.11+, FastAPI, SQLAlchemy, SQLite, Pydantic v2
* **Frontend:** React 18, TypeScript, Vite
* **Speech to text and LLM:** pluggable adapters (OpenAI for transcription, Anthropic or OpenAI for extraction), plus a demo mode that needs no API keys

## Where the code is

Everything lives in [`voice-ops-agent/`](voice-ops-agent):

```
voice-ops-agent/
  backend/    FastAPI app, extraction pipeline, missing-info checker, tests
  frontend/   React dashboard
```

The full guide (setup, demo mode, providers, privacy, tests and known limitations) is in
[`voice-ops-agent/README.md`](voice-ops-agent/README.md).

## Quick start (demo mode)

```bash
cd voice-ops-agent/backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then set AUTH_PASSWORD
python -m app.cli init-db
python -m app.cli seed               # optional: 7 sample reports
uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd voice-ops-agent/frontend
npm install
npm run dev                          # http://localhost:5173
```

Sign in as `manager` with the `AUTH_PASSWORD` from your `.env`.

## Tests

```bash
cd voice-ops-agent/backend
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests run on every push through GitHub Actions and never call a paid API.
