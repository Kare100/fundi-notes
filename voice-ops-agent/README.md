# Field Reports: voice notes to operations reports

A working MVP that turns technicians' voice notes into structured job reports for small field-service
businesses. A manager uploads a recording, the app transcribes it, extracts the job details, flags what is
missing or contradictory, writes a concise report, and shows everything on a dashboard with a daily
executive summary.

* **Backend:** Python 3.11+, FastAPI, SQLAlchemy, SQLite, Pydantic v2
* **Frontend:** React 18, TypeScript, Vite
* **Speech to text and LLM:** pluggable adapters. Ships with OpenAI (transcription), Anthropic and OpenAI (extraction), and a clearly labelled demo mode that needs no keys.

## Quick start (demo mode, no API keys)

You need Python 3.11+, Node 18+ and (recommended) `ffmpeg`, which provides `ffprobe` for audio validation.

```bash
# 1. Backend
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
#    edit .env and set AUTH_PASSWORD to something of your own
python -m app.cli init-db            # creates data/app.db
python -m app.cli seed               # optional: loads the 7 sample reports

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev                          # opens on http://localhost:5173

# 3. Backend server (back in the first terminal)
uvicorn app.main:app --reload        # http://127.0.0.1:8000
```

Open <http://localhost:5173> and sign in as `manager` with the `AUTH_PASSWORD` from your `.env`.

**Single process alternative:** run `npm run build` in `frontend/`, then only start `uvicorn`. The backend serves the built app at <http://127.0.0.1:8000>.

### What demo mode does and does not do

Demo mode is the default when no keys are configured, and the interface says so everywhere it applies.

* **Audio upload is refused**, with an explanation. The app never pretends audio was transcribed.
* **Sample transcripts and pasted text work end to end.** They are processed by a *mock extractor*:
  the 7 built-in samples return pre-written extractions; any other text gets a small set of regex rules
  (technician name, job number, a few status phrases) and a flag telling you to review everything.
  It is not an AI model.
* Everything created in demo mode carries a "demo" badge and a note in the report's source confidence section.

## Using real transcription and AI

Edit `backend/.env`, then restart the server. Nothing else changes.

| Goal | Settings |
|---|---|
| Real speech to text (OpenAI) | `TRANSCRIPTION_PROVIDER=openai`, `OPENAI_API_KEY=...` (optionally `OPENAI_TRANSCRIBE_MODEL`, `TRANSCRIBE_LANGUAGE`) |
| Real extraction with Anthropic | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` (model in `ANTHROPIC_MODEL`) |
| Real extraction with OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...`, `OPENAI_LLM_MODEL=<a chat model you have access to>` |

The server refuses to start with a clear message if a selected provider has no key. Model names change
over time: if you see "could not find the configured model", update the model setting.

**Languages and accents.** `TRANSCRIBE_LANGUAGE` is blank by default (auto detect). `TRANSCRIBE_PROMPT` primes the
speech model for Kenyan English, Swahili and Sheng. Mixed language speech is supported to the extent the chosen
provider supports it. This has been tested only with written Swahili/English transcripts, not real audio
(see Limitations), so check mixed language recordings against the transcript. Low confidence is flagged.

### Adding another provider

1. Speech to text: subclass `TranscriptionProvider` (`app/services/transcription/base.py`), implement `transcribe`.
2. LLM: subclass `LLMProvider` (`app/services/llm/base.py`), implement `complete_json`.
3. Register it in `app/services/registry.py` and add its name to the `Literal` in `app/config.py`.

Prompts, schema validation and safety checks are shared, so nothing else needs to change.

## How a report is produced

1. **Upload validation:** extension, size (streamed, never fully in memory), real file signature, ffprobe check for corruption, empty or too-long audio.
2. **Transcription:** stored once as the *original transcript*. It is never modified by edits.
3. **Extraction:** the LLM must return JSON matching a Pydantic schema. Invalid output gets one automatic repair attempt, then the report is marked failed (the transcript is kept, and **Reprocess** retries).
4. **Safety guards in code, not just in the prompt.** A name, reference or location that is not in the transcript is dropped; equipment and parts must be traceable to words in the transcript; a job is only "Completed" if the technician said so and a supporting quote exists; owners and deadlines must appear in the transcript. Each removal creates a flag showing what the AI proposed.
5. **Evidence:** each value keeps supporting quotes, checked word for word against the transcript. Click a quote to highlight it in the transcript. Unfound quotes are marked unverified. Fields are labelled *stated*, *interpreted* or *not provided*.
6. **Missing information checker** (`backend/config/checklist.json`): required fields, optionally depending on job status; contradictions (completed with outstanding work, completed but untested); ambiguous relative dates ("today", "kesho"); actions without an owner; plus the AI's own contradictions and ambiguities. Each gap has a short specific question. Information already given is not asked again. Managers mark each item *confirmed*, *corrected*, *not applicable* or *still outstanding*. Filling in a missing value closes its flag automatically; a manager's decision is never overridden.
7. **Questions are shown only on the dashboard.** The app sends no WhatsApp messages, emails or customer communications.

### Customising the checklist

Edit `backend/config/checklist.json` and restart. Each entry has `field`, `question`, `severity` and an optional `when_status` list. Field names are validated at startup.

### Daily executive summary

Built directly from saved reports with plain code, with no AI rewriting, so totals cannot be invented. It lists jobs reported, work completed, in progress, blocked (with reasons), missing information, outstanding actions with owners and deadlines, and items needing intervention. It states up front how many reports failed, are still processing, or are unreviewed AI output, and that it only covers recordings uploaded to this system. Copy it or download it as a `.txt` file. A date range is supported through the API (`/api/summary?date=...&end=...`).

## Privacy and security

* **Authentication boundary:** one shared manager login (HTTP Basic) protecting every endpoint except `/api/health`. There are no per-user accounts, roles or rate limiting. Basic credentials are only safe over **HTTPS**, so put the app behind a TLS reverse proxy for anything beyond local use. `APP_ENV=production` refuses weak passwords.
* **Secrets** are read only from the backend environment. They never reach the frontend, logs or API responses; error messages shown to users never include keys or transcript text.
* **Uploaded audio** is stored under random names in a private folder (mode 700/600), not served by any static route, and is deleted when processing ends (`AUDIO_RETENTION=delete_after_processing`, default) or after `AUDIO_RETENTION_DAYS` (`keep_days`). Retained audio is only playable through the authenticated API. Expired audio is purged at startup, or with `python -m app.cli purge-audio`, or `POST /api/maintenance/purge-audio` (schedule this daily with cron if you use `keep_days`).
* **What goes to third parties** (shown in the app under "What data leaves this server?"):
  * `TRANSCRIPTION_PROVIDER=openai`: the **audio file** is sent to OpenAI.
  * `LLM_PROVIDER=anthropic` or `openai`: the **transcript text** is sent to that provider. With `REDACT_CONTACT_DETAILS=true` (default) email addresses and phone numbers are replaced with placeholders first. Names, places and everything else are still sent.
  * Demo mode sends nothing anywhere. Reports, edits and transcripts stay in your SQLite file.
* No call recording, no monitoring, no outbound messages: only files a user deliberately uploads are processed.
* The database and transcripts are **not encrypted at rest**. Protect the machine and `backend/data/`.
* Check your own legal obligations (for example Kenya's Data Protection Act) before processing customer information and your technicians' voices with third-party services.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
```

129 tests, none of which call a paid API or need credentials. They cover: successful upload and processing; invalid, empty, oversized, corrupted, renamed and too-long audio; missing and contradictory details; fabricated names, parts, owners, deadlines and statuses; AI output failing schema validation (and the repair retry); report editing with transcript preservation; flag and action handling; filters and dashboard counts; executive summary content and honesty about failures; provider failures (timeouts, bad keys, rate limits, empty responses, with no secret leakage); retention; authentication; redaction; and demo mode without credentials. The real OpenAI and Anthropic adapters are tested against a mocked HTTP transport.

The frontend is covered by `npm run build` (strict TypeScript) and was exercised in a headless browser (login, filters, edit and save, flag resolution, actions, quote highlighting, summary download, mobile width). There are no automated frontend tests in the repository.

## Project layout

```
backend/
  app/
    main.py            FastAPI app, startup checks, serves frontend/dist if built
    config.py          settings (.env) and checklist loading
    models.py          Report, Transcript, ExtractedField, Flag, Action
    schemas.py         API request/response models
    extraction_schema.py   schema the LLM output must satisfy
    fields.py          field definitions and statuses (single source of truth)
    auth.py  database.py  cli.py
    routers/           system.py (health, config, samples), reports.py (everything else)
    services/
      audio.py         upload validation and storage
      pipeline.py      transcript -> extraction -> safety guards -> saved report
      checker.py       missing / contradictory / ambiguous checks
      summary.py       dashboard stats and daily executive summary
      views.py         JOB REPORT rendering
      prompts.py  text_utils.py  samples.py  registry.py
      transcription/   base, demo, openai_provider
      llm/             base, mock, anthropic_provider, openai_provider
  config/checklist.json
  sample_data/samples.json   7 sample transcripts (+ demo extractions)
  tests/
  .env.example  requirements.txt  requirements-dev.txt
frontend/
  src/ App.tsx  api.ts  types.ts  util.ts  styles.css  components/
```

### Sample dataset

`backend/sample_data/samples.json` holds 7 written transcripts that exercise the main cases: a complete job, "done" with customer and testing missing, a blocked job, a return visit with a stated deadline, a contradiction, mixed Swahili and English, and a vague note. `python -m app.cli seed` loads them (`--reset` clears existing reports first). They are also available in the app under **Add voice note, Sample transcripts**. There are no audio samples: use your own recordings once a provider is configured.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot reach the server" in the browser | Backend isn't running. Start `uvicorn app.main:app --reload` in `backend/` (port 8000). |
| Login says incorrect | Use the `AUTH_USERNAME` / `AUTH_PASSWORD` from `backend/.env`. Restart the server after editing `.env`. |
| Server exits with `TRANSCRIPTION_PROVIDER=openai but OPENAI_API_KEY is not set` | Set the key, or switch the provider back to `mock`. |
| Upload says "Demo mode ... cannot be transcribed" | Expected without a speech provider. Use sample transcripts or configure one. |
| "Unsupported file type" / "does not look like a real .mp3" | Allowed: mp3, wav, m4a, webm, ogg. The file contents must match its extension. |
| "could not be read as audio" for a valid file | Install `ffmpeg` (provides `ffprobe`). Without it the corruption and duration checks are skipped, with a log warning. |
| "File is too large" | Raise `MAX_UPLOAD_MB` (and check your provider's own limit, 25 MB for OpenAI). |
| Report shows "Failed" at extraction | Read the message on the report. Fix the key or model name, then press **Reprocess** (the transcript is kept). |
| "did not match the required format" | The model returned invalid JSON twice. Try Reprocess, or a stronger model. |
| Reports stuck "Processing" | A server restart interrupted them. They are marked failed on next start; press Reprocess. |
| Frontend dev server shows blank data | The Vite proxy expects the backend on `127.0.0.1:8000`; change `frontend/vite.config.ts` if you use another port. |
| `pip install` fails on Python < 3.11 | Use Python 3.11 or newer. |
| Reset everything | Stop the server, delete `backend/data/`, run `python -m app.cli init-db`. |

## Known limitations

* **Real transcription and real LLM extraction have not been run against the live services**, because no API keys were available while building this. The adapters are tested against mocked HTTP responses that follow the providers' documented formats. Expect to spend a few minutes confirming them with your own keys, and note that extraction *quality* on real recordings is untested. The mock extractor demonstrates the pipeline and guards, not the accuracy of a real model.
* Processing runs in a FastAPI background task inside the server process, which is fine for a small team. Reports interrupted by a restart are marked failed. A job queue (for example Celery or RQ) would be the next step for higher volume.
* Audio longer than a provider's own limit may fail even if it passes local checks; long recordings are not split.
* Single shared login, no audit trail of *who* edited what (only when), no encryption at rest, no rate limiting.
* Entity checks require names, references and locations to appear in the transcript. If speech-to-text mishears a name, the correct name is flagged for confirmation rather than guessed.
* Spoken numbers ("ninety two twelve") are kept as spoken and flagged, not converted.
* Dates such as "today" or "kesho" are kept as spoken and flagged; the app does not guess calendar dates. The report's "reported on" date (editable) is the only calendar anchor.
* Deadlines are free text; only ISO dates (`2026-10-31`) can be marked overdue.
* SQLite is used for the MVP; there are no migrations, so schema changes during development mean resetting the database.
* No automated frontend tests; the interface was verified by hand and with a scripted headless browser run.
