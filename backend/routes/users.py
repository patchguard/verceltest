"""Player profiles. Identity comes from Supabase Auth; the profile holds game data."""

from fastapi import APIRouter, Depends, HTTPException

from ..database import Conflict, Row, Store, get_store
from ..models import ProfileIn, UserOut
from ..security import AuthUser, current_profile, current_user

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def get_me(profile: Row = Depends(current_profile)):
    return profile


@router.post("/me", response_model=UserOut, status_code=201)
def create_me(body: ProfileIn, user: AuthUser = Depends(current_user), store: Store = Depends(get_store)):
    if store.get_user(user.id) is not None:
        raise HTTPException(409, "profile already exists")
    try:
        return store.create_user(user.id, body.username)
    except Conflict as exc:
        raise HTTPException(409, "username taken") from exc


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileIn, profile: Row = Depends(current_profile), store: Store = Depends(get_store)):
    try:
        return store.update_username(profile["id"], body.username)
    except Conflict as exc:
        raise HTTPException(409, "username taken") from exc
