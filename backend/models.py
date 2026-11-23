"""Request / response schemas."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

USERNAME_PATTERN = r"^[A-Za-z0-9_]{3,20}$"
LevelStatus = Literal["pending", "approved", "rejected"]


# ---- users -----------------------------------------------------------------
class ProfileIn(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)


class UserOut(BaseModel):
    id: UUID
    username: str
    role: Literal["player", "admin"]
    created_at: datetime


# ---- scores ----------------------------------------------------------------
class ScoreIn(BaseModel):
    score: int = Field(ge=0)
    duration_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    level_id: UUID | None = Field(default=None, description="Omit for the global board")
    client_version: str | None = Field(default=None, max_length=32)


class ScoreResult(BaseModel):
    submission_id: int
    board: str
    accepted: bool
    new_best: bool
    best_score: int | None


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: UUID
    username: str
    score: int
    duration_ms: int
    achieved_at: datetime


class LeaderboardPage(BaseModel):
    board: str
    items: list[LeaderboardEntry]
    limit: int
    offset: int


# ---- levels ----------------------------------------------------------------
class LevelIn(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    data: dict[str, Any]


class LevelSummary(BaseModel):
    id: UUID
    author_id: UUID
    author_name: str
    title: str
    description: str
    status: LevelStatus
    created_at: datetime


class LevelOut(LevelSummary):
    data: dict[str, Any]
    review_note: str | None = None
    reviewed_at: datetime | None = None


class LevelPage(BaseModel):
    items: list[LevelSummary]
    limit: int
    offset: int


class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)
