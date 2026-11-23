"""Community levels: browse, upload, and admin review."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..config import Settings, get_settings
from ..database import Row, Store, get_store
from ..models import LevelIn, LevelOut, LevelPage, LevelStatus, ReviewIn
from ..security import AuthUser, current_profile, optional_user, require_admin

router = APIRouter(prefix="/api/levels", tags=["levels"])
admin_router = APIRouter(prefix="/api/admin/levels", tags=["admin"])


@router.get("", response_model=LevelPage)
def list_levels(
    response: Response,
    q: str | None = Query(None, max_length=64, description="Search in title"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    store: Store = Depends(get_store),
):
    rows = store.list_levels(status="approved", author_id=None, q=q, limit=limit, offset=offset)
    response.headers["Cache-Control"] = "public, s-maxage=30, stale-while-revalidate=120"
    return LevelPage(items=rows, limit=limit, offset=offset)


@router.get("/mine", response_model=LevelPage)
def my_levels(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    profile: Row = Depends(current_profile),
    store: Store = Depends(get_store),
):
    rows = store.list_levels(status=None, author_id=profile["id"], q=None, limit=limit, offset=offset)
    return LevelPage(items=rows, limit=limit, offset=offset)


@router.get("/{level_id}", response_model=LevelOut)
def get_level(
    level_id: UUID,
    user: AuthUser | None = Depends(optional_user),
    store: Store = Depends(get_store),
):
    level = store.get_level(level_id)
    if level is None:
        raise HTTPException(404, "level not found")
    if level["status"] != "approved":
        # Unapproved levels are visible only to their author and admins.
        is_author = user is not None and user.id == level["author_id"]
        viewer = store.get_user(user.id) if user and not is_author else None
        if not is_author and not (viewer and viewer["role"] == "admin"):
            raise HTTPException(404, "level not found")
    return level


@router.post("", response_model=LevelOut, status_code=201)
def upload_level(
    body: LevelIn,
    profile: Row = Depends(current_profile),
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    size = len(json.dumps(body.data, separators=(",", ":")).encode())
    if size > settings.max_level_bytes:
        raise HTTPException(413, f"level data too large ({size} > {settings.max_level_bytes} bytes)")
    if store.count_levels_since(profile["id"], 3600) >= settings.level_uploads_per_hour:
        raise HTTPException(429, "upload limit reached, try again later", headers={"Retry-After": "3600"})
    return store.create_level(profile["id"], body.title.strip(), body.description.strip(), body.data)


# ---- admin ------------------------------------------------------------------
@admin_router.get("", response_model=LevelPage)
def admin_list_levels(
    status: LevelStatus = "pending",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: Row = Depends(require_admin),
    store: Store = Depends(get_store),
):
    rows = store.list_levels(status=status, author_id=None, q=None, limit=limit, offset=offset)
    return LevelPage(items=rows, limit=limit, offset=offset)


def _review(level_id: UUID, status: str, body: ReviewIn | None, admin: Row, store: Store) -> Row:
    level = store.review_level(level_id, status, admin["id"], body.note if body else None)
    if level is None:
        raise HTTPException(404, "level not found")
    return level


@admin_router.post("/{level_id}/approve", response_model=LevelOut)
def approve_level(level_id: UUID, body: ReviewIn | None = None,
                  admin: Row = Depends(require_admin), store: Store = Depends(get_store)):
    return _review(level_id, "approved", body, admin, store)


@admin_router.post("/{level_id}/reject", response_model=LevelOut)
def reject_level(level_id: UUID, body: ReviewIn | None = None,
                 admin: Row = Depends(require_admin), store: Store = Depends(get_store)):
    return _review(level_id, "rejected", body, admin, store)
