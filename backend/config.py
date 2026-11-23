"""Settings loaded from environment variables (read once per process)."""

import os
from dataclasses import dataclass
from functools import lru_cache


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str
    allow_memory_store: bool
    supabase_url: str
    supabase_jwt_secret: str
    jwt_audience: str
    cors_origins: list[str]
    max_score: int
    max_points_per_second: int
    min_duration_ms: int
    score_submissions_per_minute: int
    level_uploads_per_hour: int
    max_level_bytes: int


@lru_cache
def get_settings() -> Settings:
    on_vercel = bool(os.getenv("VERCEL"))
    return Settings(
        database_url=os.getenv("DATABASE_URL", "").strip(),
        # On Vercel memory is not shared between invocations, so refuse to
        # silently fall back to it unless explicitly allowed.
        allow_memory_store=os.getenv("ALLOW_MEMORY_STORE", "0" if on_vercel else "1") == "1",
        supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_jwt_secret=os.getenv("SUPABASE_JWT_SECRET", "").strip(),
        jwt_audience=os.getenv("JWT_AUDIENCE", "authenticated"),
        cors_origins=_list(
            "CORS_ORIGINS",
            "https://wickbound.online,https://www.wickbound.online,"
            "http://localhost:3000,http://localhost:5500,http://127.0.0.1:5500",
        ),
        max_score=_int("MAX_SCORE", 1_000_000),
        max_points_per_second=_int("MAX_POINTS_PER_SECOND", 500),
        min_duration_ms=_int("MIN_DURATION_MS", 1_000),
        score_submissions_per_minute=_int("SCORE_SUBMISSIONS_PER_MINUTE", 10),
        level_uploads_per_hour=_int("LEVEL_UPLOADS_PER_HOUR", 5),
        max_level_bytes=_int("MAX_LEVEL_BYTES", 64 * 1024),
    )
