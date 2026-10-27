"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BACKEND_DIR, check_startup, get_checklist, get_settings
from .database import get_sessionmaker, init_db
from .routers import reports, system
from .services.pipeline import mark_interrupted, purge_expired_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("voiceops")

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
UPLOAD_SLACK_BYTES = 2 * 1024 * 1024  # multipart overhead allowance


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    for warning in check_startup(settings):
        logger.warning(warning)
    get_checklist()  # fail fast if the checklist file is broken
    init_db()
    with get_sessionmaker()() as db:
        if (n := mark_interrupted(db)):
            logger.warning("Marked %s interrupted report(s) as failed", n)
        if (n := purge_expired_audio(db)):
            logger.info("Purged %s expired audio file(s)", n)
    yield


app = FastAPI(title="Voice-to-Operations Reporting Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    """Cheap early rejection using Content-Length (the upload handler enforces the real limit while streaming)."""
    if request.method == "POST" and request.url.path == "/api/reports/upload":
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > get_settings().max_upload_bytes + UPLOAD_SLACK_BYTES:
            return JSONResponse({"detail": f"File is too large. The limit is {get_settings().max_upload_mb} MB."}, status_code=413)
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse({"detail": "Internal server error."}, status_code=500)


app.include_router(system.router)
app.include_router(reports.router)

# Optional single-process mode: serve the built React app (run `npm run build` in frontend/).
if Path(FRONTEND_DIST).is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
