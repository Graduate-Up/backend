import hashlib
import sqlite3

from fastapi.testclient import TestClient

from app.services.accounts import get_account_store
from tests.demo_app import create_demo_app

CREDENTIALS = {"username": "student-one", "password": "sample-password"}
PROFILE = {
    "student_number": "2024000123", "admission_year": 2024, "major": "aicom",
    "track": "test-track", "expected_graduation_date": "2028-02",
}


def sign_in(client, credentials=CREDENTIALS):
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_profile_is_restored_on_relogin_and_isolated_by_account(monkeypatch, tmp_path):
    db = tmp_path / "accounts.sqlite3"
    monkeypatch.setenv("JOLUP_DB_PATH", str(db))
    with TestClient(create_demo_app()) as client:
        page = client.get("/account")
        assert page.status_code == 200
        assert 'id="show-requirements"' in page.text
        script = client.get("/requirements-view.js")
        assert script.status_code == 200
        assert "export function renderRequirements" in script.text
        assert client.get("/api/v1/me").status_code == 401
        assert client.post("/api/v1/auth/register", json=CREDENTIALS).status_code == 201
        assert client.post("/api/v1/auth/register", json={
            **CREDENTIALS, "username": CREDENTIALS["username"].upper(),
        }).status_code == 409
        assert client.post("/api/v1/auth/login", json={**CREDENTIALS, "password": "wrong-password"}).status_code == 401
        headers = sign_in(client)
        assert client.get("/api/v1/me", headers=headers).json()["profile"] is None
        assert client.get("/api/v1/me/requirements", headers=headers).status_code == 409
        assert client.put("/api/v1/me/profile", json={**PROFILE, "track": "bad"}, headers=headers).status_code == 422
        assert client.get("/api/v1/me", headers=headers).json()["profile"] is None

        response = client.put("/api/v1/me/profile", json=PROFILE, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        saved = response.json()["profile"]
        assert {key: saved[key] for key in PROFILE} == PROFILE
        assert client.get("/api/v1/me/requirements", headers=headers).json()["id"] == "test-aicom"
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/me", headers=headers).status_code == 401

    get_account_store.cache_clear()
    with TestClient(create_demo_app()) as client:
        headers = sign_in(client)
        assert client.get("/api/v1/me", headers=headers).json()["profile"] == saved
        assert client.get("/api/v1/me/requirements", headers=headers).json()["id"] == "test-aicom"
        assert client.put("/api/v1/me/profile", json={**PROFILE, "track": None}, headers=headers).status_code == 200
        assert client.get("/api/v1/me", headers=headers).json()["profile"]["track"] is None
        other = {**CREDENTIALS, "username": "student-two"}
        assert client.post("/api/v1/auth/register", json=other).status_code == 201
        other_headers = sign_in(client, other)
        assert client.get("/api/v1/me", headers=other_headers).json()["profile"] is None

        with sqlite3.connect(db) as conn:
            token = headers["Authorization"].removeprefix("Bearer ")
            token_hash = hashlib.sha256(token.encode()).digest()
            assert conn.execute("UPDATE sessions SET expires_at = 0 WHERE token_hash = ?", (token_hash,)).rowcount == 1
            hashes = conn.execute("SELECT password_hash, password_salt FROM users ORDER BY id").fetchall()
            assert hashes[0][0] != hashes[1][0]
        assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_validation_does_not_reflect_password(monkeypatch, tmp_path):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        invalid = client.post("/api/v1/auth/register", json={**CREDENTIALS, "password": "abc321"})
        assert invalid.status_code == 422
        assert "abc321" not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/v1/me"]["get"]["security"] == [{"HTTPBearer": []}]

