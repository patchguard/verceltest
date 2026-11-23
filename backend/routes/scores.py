"""Leaderboards and score submission."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..config import Settings, get_settings
from ..database import Row, Store, get_store
from ..models import LeaderboardEntry, LeaderboardPage, ScoreIn, ScoreResult
from ..security import current_profile

router = APIRouter(prefix="/api/scores", tags=["scores"])

GLOBAL_BOARD = "global"


def board_key(level_id: UUID | None) -> str:
    return str(level_id) if level_id else GLOBAL_BOARD


def plausibility_error(body: ScoreIn, s: Settings) -> str | None:
    """Cheap server-side sanity checks. Not a substitute for replay validation."""
    if body.score > s.max_score:
        return f"score exceeds maximum ({s.max_score})"
    if body.duration_ms < s.min_duration_ms:
        return "run too short"
    seconds = body.duration_ms / 1000
    if body.score > s.max_points_per_second * seconds:
        return "score rate implausible"
    return None


@router.get("", response_model=LeaderboardPage)
def get_leaderboard(
    response: Response,
    level_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    store: Store = Depends(get_store),
):
    board = board_key(level_id)
    rows = store.leaderboard(board, limit, offset)
    # Let Vercel's CDN absorb read traffic for a few seconds.
    response.headers["Cache-Control"] = "public, s-maxage=10, stale-while-revalidate=30"
    return LeaderboardPage(board=board, items=rows, limit=limit, offset=offset)


@router.get("/me", response_model=LeaderboardEntry)
def get_my_best(
    response: Response,
    level_id: UUID | None = None,
    profile: Row = Depends(current_profile),
    store: Store = Depends(get_store),
):
    response.headers["Cache-Control"] = "private, no-store"
    row = store.player_best(board_key(level_id), profile["id"])
    if row is None:
        raise HTTPException(404, "no score on this board yet")
    return row


@router.post("", response_model=ScoreResult, status_code=201)
def submit_score(
    body: ScoreIn,
    profile: Row = Depends(current_profile),
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    if store.count_submissions_since(profile["id"], 60) >= settings.score_submissions_per_minute:
        raise HTTPException(429, "too many submissions, slow down", headers={"Retry-After": "60"})

    if body.level_id is not None:
        level = store.get_level(body.level_id)
        if level is None or level["status"] != "approved":
            raise HTTPException(404, "level not found")

    reason = plausibility_error(body, settings)
    board = board_key(body.level_id)
    result = store.record_submission(
        user_id=profile["id"], board=board, level_id=body.level_id, score=body.score,
        duration_ms=body.duration_ms, client_version=body.client_version, reject_reason=reason,
    )
    if reason:
        raise HTTPException(422, {"message": "score rejected", "reason": reason,
                                  "submission_id": result["submission_id"]})
    return ScoreResult(board=board, accepted=True, **result)
