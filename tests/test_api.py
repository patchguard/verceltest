import dataclasses
import os
import time
import uuid

os.environ["DATABASE_URL"] = ""
os.environ["SUPABASE_JWT_SECRET"] = "test-secret-at-least-32-bytes-long!!"
os.environ["SCORE_SUBMISSIONS_PER_MINUTE"] = "5"

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from backend import database  # noqa: E402

client = TestClient(app)


PG_URL = os.getenv("TEST_DATABASE_URL", "")  # e.g. postgresql://postgres@localhost:5432/wickbound
_mode = {"pg": False}


@pytest.fixture(autouse=True, params=["memory", "postgres"])
def fresh_store(request, monkeypatch):
    """Run every test against the in-memory store, and against Postgres if TEST_DATABASE_URL is set."""
    _mode["pg"] = request.param == "postgres"
    if not _mode["pg"]:
        monkeypatch.setattr(database, "_memory_store", database.MemoryStore())
        return
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("truncate leaderboard, score_submissions, levels, users, auth.users cascade")
    settings = dataclasses.replace(database.get_settings(), database_url=PG_URL)
    monkeypatch.setattr(database, "get_settings", lambda: settings)


def _pg_exec(sql: str, params: tuple) -> None:
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute(sql, params)


def token(sub: uuid.UUID, **overrides) -> str:
    claims = {"sub": str(sub), "aud": "authenticated", "exp": int(time.time()) + 3600, **overrides}
    return jwt.encode(claims, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")


def auth(sub: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {token(sub)}"}


def new_identity() -> uuid.UUID:
    """A fresh Supabase Auth user id (registered in auth.users for the Postgres run)."""
    uid = uuid.uuid4()
    if _mode["pg"]:
        _pg_exec("insert into auth.users (id) values (%s)", (uid,))
    return uid


def make_player(name: str, admin: bool = False) -> dict:
    uid = new_identity()
    r = client.post("/api/users/me", json={"username": name}, headers=auth(uid))
    assert r.status_code == 201, r.text
    if admin:
        if _mode["pg"]:
            _pg_exec("update users set role = 'admin' where id = %s", (uid,))
        else:
            database._memory_store.users[uid]["role"] = "admin"
    return auth(uid)


def test_health():
    r = client.get("/api/health")
    assert r.json() == {"status": "ok", "store": "postgres" if _mode["pg"] else "memory"}


def test_auth_required_and_bad_tokens():
    assert client.get("/api/users/me").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/api/users/me", headers=bad).status_code == 401
    expired = {"Authorization": f"Bearer {token(uuid.uuid4(), exp=1)}"}
    assert client.get("/api/users/me", headers=expired).status_code == 401
    wrong_aud = {"Authorization": f"Bearer {token(uuid.uuid4(), aud='other')}"}
    assert client.get("/api/users/me", headers=wrong_aud).status_code == 401


def test_asymmetric_supabase_token(monkeypatch):
    """ES256 tokens are verified via JWKS even when the legacy HS256 secret is also configured."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from backend import config, security

    url = "https://proj.supabase.co"
    settings = dataclasses.replace(config.get_settings(), supabase_url=url)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    private = ec.generate_private_key(ec.SECP256R1())

    class FakeJWKS:
        def get_signing_key_from_jwt(self, _token):
            return type("K", (), {"key": private.public_key()})()

    monkeypatch.setattr(security, "_jwks_client", lambda _url: FakeJWKS())

    def es_token(**overrides):
        claims = {"sub": str(new_identity()), "aud": "authenticated", "iss": f"{url}/auth/v1",
                  "exp": int(time.time()) + 3600, **overrides}
        return {"Authorization": f"Bearer {jwt.encode(claims, private, algorithm='ES256')}"}

    assert client.get("/api/users/me", headers=es_token()).status_code == 403  # valid, no profile yet
    assert client.get("/api/users/me", headers=es_token(iss="https://other.supabase.co/auth/v1")).status_code == 401
    unsigned = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated"}, None, algorithm="none")
    assert client.get("/api/users/me", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401


def test_project_url_normalization():
    from backend.config import _project_url

    for raw in ["https://x.supabase.co", "https://x.supabase.co/", "https://x.supabase.co/rest/v1/",
                " https://x.supabase.co/auth/v1 "]:
        assert _project_url(raw) == "https://x.supabase.co"


def test_profile_flow():
    uid = new_identity()
    assert client.get("/api/users/me", headers=auth(uid)).status_code == 403
    assert client.post("/api/users/me", json={"username": "x"}, headers=auth(uid)).status_code == 422
    r = client.post("/api/users/me", json={"username": "alice"}, headers=auth(uid))
    assert r.status_code == 201 and r.json()["role"] == "player"
    assert client.get("/api/users/me", headers=auth(uid)).json()["username"] == "alice"
    assert client.post("/api/users/me", json={"username": "ALICE"}, headers=auth(new_identity())).status_code == 409
    assert client.patch("/api/users/me", json={"username": "alice2"}, headers=auth(uid)).status_code == 200


def test_scores_leaderboard_and_best():
    a, b = make_player("alice"), make_player("bob")
    ok = client.post("/api/scores", json={"score": 100, "duration_ms": 10_000}, headers=a)
    assert ok.status_code == 201 and ok.json()["new_best"] is True
    lower = client.post("/api/scores", json={"score": 50, "duration_ms": 10_000}, headers=a).json()
    assert lower["new_best"] is False and lower["best_score"] == 100
    client.post("/api/scores", json={"score": 300, "duration_ms": 10_000}, headers=b)

    board = client.get("/api/scores")
    assert board.headers["cache-control"].startswith("public")
    items = board.json()["items"]
    assert [(i["username"], i["score"], i["rank"]) for i in items] == [("bob", 300, 1), ("alice", 100, 2)]
    assert client.get("/api/scores/me", headers=a).json()["rank"] == 2


def test_score_anti_cheat_and_rate_limit():
    a = make_player("cheater")
    for body, reason in [
        ({"score": 10_000_000, "duration_ms": 999_999}, "maximum"),
        ({"score": 10, "duration_ms": 10}, "too short"),
        ({"score": 100_000, "duration_ms": 2_000}, "implausible"),
    ]:
        r = client.post("/api/scores", json=body, headers=a)
        assert r.status_code == 422 and reason in r.json()["detail"]["reason"]
    assert client.get("/api/scores").json()["items"] == []
    client.post("/api/scores", json={"score": 1, "duration_ms": 5_000}, headers=a)
    client.post("/api/scores", json={"score": 1, "duration_ms": 5_000}, headers=a)
    assert client.post("/api/scores", json={"score": 1, "duration_ms": 5_000}, headers=a).status_code == 429


def test_level_lifecycle():
    author, other, admin = make_player("maker"), make_player("viewer"), make_player("mod", admin=True)
    r = client.post("/api/levels", json={"title": "Cave", "data": {"tiles": [1, 2, 3]}}, headers=author)
    assert r.status_code == 201 and r.json()["status"] == "pending"
    lid = r.json()["id"]

    # pending: hidden from public & other players, visible to author and admins
    assert client.get("/api/levels").json()["items"] == []
    assert client.get(f"/api/levels/{lid}").status_code == 404
    assert client.get(f"/api/levels/{lid}", headers=other).status_code == 404
    assert client.get(f"/api/levels/{lid}", headers=author).status_code == 200
    assert client.get(f"/api/levels/{lid}", headers=admin).status_code == 200
    assert len(client.get("/api/levels/mine", headers=author).json()["items"]) == 1

    # cannot score on an unapproved level
    assert client.post("/api/scores", json={"score": 1, "duration_ms": 5000, "level_id": lid},
                       headers=other).status_code == 404

    assert client.post(f"/api/admin/levels/{lid}/approve", headers=other).status_code == 403
    assert len(client.get("/api/admin/levels", headers=admin).json()["items"]) == 1
    r = client.post(f"/api/admin/levels/{lid}/approve", json={"note": "nice"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "approved"

    assert client.get("/api/levels", params={"q": "cav"}).json()["items"][0]["author_name"] == "maker"
    r = client.post("/api/scores", json={"score": 42, "duration_ms": 5000, "level_id": lid}, headers=other)
    assert r.status_code == 201 and r.json()["board"] == lid
    assert client.get("/api/scores", params={"level_id": lid}).json()["items"][0]["score"] == 42
    assert client.get("/api/scores").json()["items"] == []


def test_level_size_limit():
    a = make_player("bigmap")
    r = client.post("/api/levels", json={"title": "Huge", "data": {"blob": "x" * 70_000}}, headers=a)
    assert r.status_code == 413


def test_cors_preflight():
    r = client.options("/api/scores", headers={
        "Origin": "https://wickbound.online", "Access-Control-Request-Method": "POST",
    })
    assert r.headers["access-control-allow-origin"] == "https://wickbound.online"
    r = client.options("/api/scores", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" not in r.headers
