"""Data access layer.

Two interchangeable stores:
* PostgresStore  - Supabase Postgres via psycopg (production).
* MemoryStore    - process-local dicts for local dev and tests. NOT persistent
                   on Vercel: each invocation may run in a different instance.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from .config import get_settings

Row = dict[str, Any]


class Conflict(Exception):
    """Unique constraint violated (e.g. username taken)."""


class Store(Protocol):
    kind: str

    def ping(self) -> None: ...
    def get_user(self, user_id: UUID) -> Row | None: ...
    def create_user(self, user_id: UUID, username: str) -> Row: ...
    def update_username(self, user_id: UUID, username: str) -> Row: ...
    def count_submissions_since(self, user_id: UUID, seconds: int) -> int: ...
    def record_submission(
        self, *, user_id: UUID, board: str, level_id: UUID | None, score: int,
        duration_ms: int, client_version: str | None, reject_reason: str | None,
    ) -> Row: ...
    def leaderboard(self, board: str, limit: int, offset: int) -> list[Row]: ...
    def player_best(self, board: str, user_id: UUID) -> Row | None: ...
    def count_levels_since(self, user_id: UUID, seconds: int) -> int: ...
    def create_level(self, author_id: UUID, title: str, description: str, data: dict) -> Row: ...
    def get_level(self, level_id: UUID) -> Row | None: ...
    def list_levels(
        self, *, status: str | None, author_id: UUID | None, q: str | None, limit: int, offset: int,
    ) -> list[Row]: ...
    def review_level(self, level_id: UUID, status: str, reviewer_id: UUID, note: str | None) -> Row | None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# In-memory store
# --------------------------------------------------------------------------- #
class MemoryStore:
    kind = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.users: dict[UUID, Row] = {}
        self.levels: dict[UUID, Row] = {}
        self.submissions: list[Row] = []
        self.board: dict[tuple[str, UUID], Row] = {}
        self._ids = count(1)

    def ping(self) -> None:
        return None

    def get_user(self, user_id):
        return self.users.get(user_id)

    def _username_taken(self, username: str, exclude: UUID | None = None) -> bool:
        return any(
            u["username"].lower() == username.lower() and u["id"] != exclude for u in self.users.values()
        )

    def create_user(self, user_id, username):
        with self._lock:
            if user_id in self.users or self._username_taken(username):
                raise Conflict("username or profile already exists")
            row = {"id": user_id, "username": username, "role": "player", "created_at": _now()}
            self.users[user_id] = row
            return row

    def update_username(self, user_id, username):
        with self._lock:
            if self._username_taken(username, exclude=user_id):
                raise Conflict("username taken")
            self.users[user_id]["username"] = username
            return self.users[user_id]

    def count_submissions_since(self, user_id, seconds):
        cutoff = _now() - timedelta(seconds=seconds)
        return sum(1 for s in self.submissions if s["user_id"] == user_id and s["created_at"] >= cutoff)

    def record_submission(self, *, user_id, board, level_id, score, duration_ms, client_version, reject_reason):
        with self._lock:
            sub = {
                "id": next(self._ids), "user_id": user_id, "board": board, "level_id": level_id,
                "score": score, "duration_ms": duration_ms, "client_version": client_version,
                "status": "rejected" if reject_reason else "accepted",
                "reject_reason": reject_reason, "created_at": _now(),
            }
            self.submissions.append(sub)
            new_best = False
            if not reject_reason:
                cur = self.board.get((board, user_id))
                if cur is None or score > cur["score"]:
                    self.board[(board, user_id)] = {
                        "board": board, "user_id": user_id, "score": score,
                        "duration_ms": duration_ms, "achieved_at": sub["created_at"],
                    }
                    new_best = True
            best = self.board.get((board, user_id))
            return {"submission_id": sub["id"], "new_best": new_best, "best_score": best["score"] if best else None}

    def _ranked(self, board: str) -> list[Row]:
        rows = sorted(
            (r for (b, _), r in self.board.items() if b == board),
            key=lambda r: (-r["score"], r["achieved_at"]),
        )
        return [{**r, "rank": i + 1, "username": self.users[r["user_id"]]["username"]} for i, r in enumerate(rows)]

    def leaderboard(self, board, limit, offset):
        return self._ranked(board)[offset:offset + limit]

    def player_best(self, board, user_id):
        return next((r for r in self._ranked(board) if r["user_id"] == user_id), None)

    def count_levels_since(self, user_id, seconds):
        cutoff = _now() - timedelta(seconds=seconds)
        return sum(1 for lv in self.levels.values() if lv["author_id"] == user_id and lv["created_at"] >= cutoff)

    def _with_author(self, lv: Row) -> Row:
        return {**lv, "author_name": self.users[lv["author_id"]]["username"]}

    def create_level(self, author_id, title, description, data):
        with self._lock:
            row = {
                "id": uuid4(), "author_id": author_id, "title": title, "description": description,
                "data": data, "status": "pending", "review_note": None, "reviewed_by": None,
                "reviewed_at": None, "created_at": _now(),
            }
            self.levels[row["id"]] = row
            return self._with_author(row)

    def get_level(self, level_id):
        lv = self.levels.get(level_id)
        return self._with_author(lv) if lv else None

    def list_levels(self, *, status, author_id, q, limit, offset):
        rows = [
            lv for lv in self.levels.values()
            if (status is None or lv["status"] == status)
            and (author_id is None or lv["author_id"] == author_id)
            and (not q or q.lower() in lv["title"].lower())
        ]
        rows.sort(key=lambda lv: lv["created_at"], reverse=True)
        return [self._with_author(lv) for lv in rows[offset:offset + limit]]

    def review_level(self, level_id, status, reviewer_id, note):
        with self._lock:
            lv = self.levels.get(level_id)
            if not lv:
                return None
            lv.update(status=status, review_note=note, reviewed_by=reviewer_id, reviewed_at=_now())
            return self._with_author(lv)


_memory_store = MemoryStore()


# --------------------------------------------------------------------------- #
# Postgres store
# --------------------------------------------------------------------------- #
class PostgresStore:
    kind = "postgres"

    _RANKED = (
        "select l.*, u.username, "
        "  rank() over (order by l.score desc, l.achieved_at asc) as rank "
        "from leaderboard l join users u on u.id = l.user_id where l.board = %s"
    )
    _LEVEL_COLS = (
        "lv.id, lv.author_id, u.username as author_name, lv.title, lv.description, lv.data, "
        "lv.status, lv.review_note, lv.reviewed_by, lv.reviewed_at, lv.created_at"
    )

    def __init__(self, conn) -> None:
        self.conn = conn

    def _one(self, sql: str, params: tuple = ()) -> Row | None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[Row]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def ping(self):
        self._one("select 1 as ok")

    def get_user(self, user_id):
        return self._one("select id, username, role, created_at from users where id = %s", (user_id,))

    def create_user(self, user_id, username):
        from psycopg.errors import UniqueViolation

        try:
            return self._one(
                "insert into users (id, username) values (%s, %s) returning id, username, role, created_at",
                (user_id, username),
            )
        except UniqueViolation as exc:
            raise Conflict(str(exc)) from exc

    def update_username(self, user_id, username):
        from psycopg.errors import UniqueViolation

        try:
            return self._one(
                "update users set username = %s where id = %s returning id, username, role, created_at",
                (username, user_id),
            )
        except UniqueViolation as exc:
            raise Conflict(str(exc)) from exc

    def count_submissions_since(self, user_id, seconds):
        row = self._one(
            "select count(*) as n from score_submissions "
            "where user_id = %s and created_at > now() - make_interval(secs => %s)",
            (user_id, seconds),
        )
        return row["n"]

    def record_submission(self, *, user_id, board, level_id, score, duration_ms, client_version, reject_reason):
        with self.conn.transaction():
            sub = self._one(
                "insert into score_submissions "
                "(user_id, board, level_id, score, duration_ms, client_version, status, reject_reason) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id, created_at",
                (user_id, board, level_id, score, duration_ms, client_version,
                 "rejected" if reject_reason else "accepted", reject_reason),
            )
            new_best = False
            if not reject_reason:
                upd = self._one(
                    "insert into leaderboard (board, user_id, score, duration_ms, submission_id, achieved_at) "
                    "values (%s, %s, %s, %s, %s, %s) "
                    "on conflict (board, user_id) do update set "
                    "  score = excluded.score, duration_ms = excluded.duration_ms, "
                    "  submission_id = excluded.submission_id, achieved_at = excluded.achieved_at "
                    "where excluded.score > leaderboard.score "
                    "returning submission_id",
                    (board, user_id, score, duration_ms, sub["id"], sub["created_at"]),
                )
                new_best = upd is not None
            best = self._one("select score from leaderboard where board = %s and user_id = %s", (board, user_id))
        return {"submission_id": sub["id"], "new_best": new_best, "best_score": best["score"] if best else None}

    def leaderboard(self, board, limit, offset):
        return self._all(f"{self._RANKED} order by rank limit %s offset %s", (board, limit, offset))

    def player_best(self, board, user_id):
        return self._one(f"select * from ({self._RANKED}) r where r.user_id = %s", (board, user_id))

    def count_levels_since(self, user_id, seconds):
        row = self._one(
            "select count(*) as n from levels "
            "where author_id = %s and created_at > now() - make_interval(secs => %s)",
            (user_id, seconds),
        )
        return row["n"]

    def create_level(self, author_id, title, description, data):
        from psycopg.types.json import Jsonb

        row = self._one(
            "insert into levels (author_id, title, description, data) values (%s, %s, %s, %s) returning id",
            (author_id, title, description, Jsonb(data)),
        )
        return self.get_level(row["id"])

    def get_level(self, level_id):
        return self._one(
            f"select {self._LEVEL_COLS} from levels lv join users u on u.id = lv.author_id where lv.id = %s",
            (level_id,),
        )

    def list_levels(self, *, status, author_id, q, limit, offset):
        where, params = [], []
        if status:
            where.append("lv.status = %s")
            params.append(status)
        if author_id:
            where.append("lv.author_id = %s")
            params.append(author_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("lv.title ilike %s")
            params.append(f"%{escaped}%")
        clause = ("where " + " and ".join(where)) if where else ""
        return self._all(
            f"select {self._LEVEL_COLS} from levels lv join users u on u.id = lv.author_id {clause} "
            "order by lv.created_at desc limit %s offset %s",
            (*params, limit, offset),
        )

    def review_level(self, level_id, status, reviewer_id, note):
        row = self._one(
            "update levels set status = %s, review_note = %s, reviewed_by = %s, reviewed_at = now() "
            "where id = %s returning id",
            (status, note, reviewer_id, level_id),
        )
        return self.get_level(level_id) if row else None


# --------------------------------------------------------------------------- #
# FastAPI dependency
# --------------------------------------------------------------------------- #
def get_store() -> Iterator[Store]:
    settings = get_settings()
    if not settings.database_url:
        if not settings.allow_memory_store:
            raise HTTPException(503, "DATABASE_URL is not configured")
        yield _memory_store
        return

    import psycopg
    from psycopg.rows import dict_row

    try:
        # prepare_threshold=None is required by Supabase's transaction pooler.
        conn = psycopg.connect(
            settings.database_url, autocommit=True, row_factory=dict_row,
            prepare_threshold=None, connect_timeout=5,
        )
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    try:
        yield PostgresStore(conn)
    finally:
        conn.close()
