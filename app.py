"""Wickbound API - FastAPI entrypoint (auto-detected by Vercel)."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.database import Store, get_store
from backend.routes import levels, scores, users

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


@app.get("/", tags=["meta"])
def root():
    return {"status": "ok", "service": "Wickbound API", "docs": "/docs"}


@app.get("/api/health", tags=["meta"])
def health(store: Store = Depends(get_store)):
    store.ping()
    return {"status": "ok", "store": store.kind}
