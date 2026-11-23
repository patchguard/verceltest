"""Wickbound API - FastAPI entrypoint (auto-detected by Vercel)."""

import logging
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend.config import get_settings
from backend.database import Store, get_store
from backend.routes import levels, scores, users

logger = logging.getLogger("wickbound")
settings = get_settings()

app = FastAPI(title="Wickbound API", version="1.0.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # auth uses Bearer tokens, not cookies
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,
)

app.include_router(users.router)
app.include_router(scores.router)
app.include_router(levels.router)
app.include_router(levels.admin_router)


FRONTEND = Path(__file__).parent / "frontend" / "index.html"


@app.get("/", include_in_schema=False)
def frontend():
    """Test console (single static page) served from the same origin as the API."""
    return FileResponse(FRONTEND, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config", tags=["meta"])
def public_config():
    """Public values the browser needs to talk to Supabase Auth. Both are safe to expose."""
    return {
        "supabase_url": settings.supabase_url or None,
        "supabase_publishable_key": settings.supabase_publishable_key or None,
    }


@app.exception_handler(psycopg.Error)
def database_error(request: Request, exc: psycopg.Error):
    # Log the full error for Vercel logs; expose only the error class to clients.
    logger.error("database error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "database error", "error": type(exc).__name__})


@app.get("/api/health", tags=["meta"])
def health(response: Response, store: Store = Depends(get_store)):
    store.ping()
    missing = store.missing_tables()
    if missing:
        response.status_code = 503
        return {"status": "degraded", "store": store.kind,
                "missing_tables": missing, "hint": "run sql/schema.sql in Supabase"}
    return {"status": "ok", "store": store.kind}
