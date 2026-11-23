import hashlib
import sqlite3

from fastapi.testclient import TestClient

from app.services.accounts import get_account_store
from tests.demo_app import create_demo_app

CREDENTIALS = {"email": "student-one@knu.ac.kr", "password": "sample-password"}
PROFILE = {
    "student_number": "2024000123", "admission_year": 2024, "major": "aicom",
    "track": "test-track", "expected_graduation_date": "2028-02",
}


def sign_in(client, credentials=CREDENTIALS):
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def register(client, mailbox, credentials=CREDENTIALS):
    email = credentials["email"]
    assert client.post("/api/v1/auth/email-code", json={"email": email}).status_code == 204
    return client.post("/api/v1/auth/register", json={**credentials, "code": mailbox[email.lower()]})


def test_profile_is_restored_on_relogin_and_isolated_by_account(monkeypatch, tmp_path, verification_mailbox):
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
        assert register(client, verification_mailbox).status_code == 201
        assert client.post("/api/v1/auth/register", json={
            **CREDENTIALS, "email": CREDENTIALS["email"].upper(), "code": verification_mailbox[CREDENTIALS["email"]],
        }).status_code == 400
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
        assert client.get("/api/v1/me/records", headers=headers).json()["completed_courses"] == []
        assert client.get("/api/v1/me/evaluation", headers=headers).json()["progress"] == 0
        transcript = {"text": '{"data":[{"sbjetCd":"TEST0123","crdit":3,"year":2024,"semester":1,"grade":"PASS","category":"major"}]}'}
        assert client.post("/api/v1/me/transcript/import", headers=headers, json={**transcript, "expected": 2}).status_code == 409
        assert client.get("/api/v1/me/records", headers=headers).json()["completed_courses"] == []
        assert client.post("/api/v1/me/transcript/import", headers=headers, json={**transcript, "expected": 1}).json()["added"] == 1
        summary = {"독서와토론": 0, "사고교육": 0, "글쓰기": 3, "외국어": 0, "수리": 3,
                   "기초과학": 0, "인문·사회": 6, "자연과학": 3}
        reimport = client.post("/api/v1/me/transcript/import", headers=headers,
                               json={**transcript, "expected": 1, "liberal_summary": summary}).json()
        assert reimport["added"] == 0
        assert reimport["liberal_summary_saved"] is True
        newer = {"text": transcript["text"].replace('"year":2024', '"year":2025')}
        assert client.post("/api/v1/me/transcript/import", headers=headers, json={**newer, "expected": 1}).json()["added"] == 0
        mixed = {"text": '{"data":[{"sbjetCd":"TEST0124","crdit":3,"year":2024,"semester":"동계계절학기",'
                 '"grade":"PASS","category":"major"},{"sbjetCd":"TEST0125","crdit":2,"year":2024,'
                 '"semester":"unknown","grade":"PASS","category":"major"}]}', "expected": 2}
        imported = client.post("/api/v1/me/transcript/import", headers=headers, json=mixed).json()
        assert imported["added"] == 1
        assert imported["needs_review"] == 1
        records = {
            "completed_courses": [{
                "course_code": "TEST001", "course_name": "Test course", "semester": "2024-1",
                "credits": 3, "transcript_category": "major", "status": "completed",
            }],
            "nonacademic_records": [],
        }
        assert client.put("/api/v1/me/records", headers=headers, json=records).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/me", headers=headers).status_code == 401

    get_account_store.cache_clear()
    with TestClient(create_demo_app()) as client:
        headers = sign_in(client)
        assert client.get("/api/v1/me", headers=headers).json()["profile"] == saved
        assert client.get("/api/v1/me/requirements", headers=headers).json()["id"] == "test-aicom"
        assert client.get("/api/v1/me/records", headers=headers).json()["completed_courses"][0]["course_code"] == "TEST001"
        assert client.get("/api/v1/me/records", headers=headers).json()["liberal_summary"] == summary
        evaluation = client.get("/api/v1/me/evaluation", headers=headers).json()
        assert 0 < evaluation["progress"] < 100
        assert evaluation["credit_entries"][0]["course_name"] == "Test course"
        assert evaluation["requirement_counts"]["unsatisfied"] > 0
        assert client.put("/api/v1/me/profile", json={**PROFILE, "track": None}, headers=headers).status_code == 200
        assert client.get("/api/v1/me", headers=headers).json()["profile"]["track"] is None
        other = {**CREDENTIALS, "email": "student-two@knu.ac.kr"}
        assert register(client, verification_mailbox, other).status_code == 201
        other_headers = sign_in(client, other)
        assert client.get("/api/v1/me", headers=other_headers).json()["profile"] is None
        assert client.get("/api/v1/me/records", headers=other_headers).json()["completed_courses"] == []
        assert client.get("/api/v1/me/evaluation", headers=other_headers).status_code == 409

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
        invalid = client.post("/api/v1/auth/register", json={**CREDENTIALS, "password": "abc321", "code": "001234"})
        assert invalid.status_code == 422
        assert "abc321" not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/v1/me"]["get"]["security"] == [{"HTTPBearer": []}, {"APIKeyCookie": []}]


def test_school_email_code_is_bound_to_the_recipient_and_attempt_limited(
    monkeypatch, tmp_path, verification_mailbox,
):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        assert client.post("/api/v1/auth/email-code", json={"email": "a@knu.ac.kr.example"}).status_code == 422
        assert client.post("/api/v1/auth/login", json=CREDENTIALS).status_code == 401
        assert client.post("/api/v1/auth/email-code", json={"email": CREDENTIALS["email"].upper()}).status_code == 204
        assert client.post("/api/v1/auth/email-code", json={"email": CREDENTIALS["email"]}).status_code == 429
        correct = verification_mailbox[CREDENTIALS["email"]]
        assert len(correct) == 6 and correct.isdecimal()
        assert client.post("/api/v1/auth/register", json={
            **CREDENTIALS, "email": "different@knu.ac.kr", "code": correct,
        }).status_code == 400
        wrong = "000000" if correct != "000000" else "000001"
        for _ in range(5):
            assert client.post("/api/v1/auth/register", json={**CREDENTIALS, "code": wrong}).status_code == 400
        assert client.post("/api/v1/auth/register", json={**CREDENTIALS, "code": correct}).status_code == 400


def test_verifying_legacy_account_preserves_saved_profile(monkeypatch, tmp_path, verification_mailbox):
    db = tmp_path / "accounts.sqlite3"
    monkeypatch.setenv("JOLUP_DB_PATH", str(db))
    store = get_account_store(db)
    from app.schemas.account import StudentSettings
    from app.services.accounts import PASSWORD_ROUNDS, _password_digest, _token_digest

    salt = b"legacy-test-salt"
    with store.connect() as conn:
        user = conn.execute(
            "INSERT INTO users(username,password_salt,password_hash,password_rounds,profile_json) VALUES (?,?,?,?,?)",
            ("old-student", salt, _password_digest(CREDENTIALS["password"], salt, PASSWORD_ROUNDS),
             PASSWORD_ROUNDS, StudentSettings(**PROFILE).model_dump_json()),
        ).lastrowid
        old_token = "A" * 43
        conn.execute("INSERT INTO sessions VALUES (?,?,?)", (_token_digest(old_token), user, 8_000_000_000))

    with TestClient(create_demo_app()) as client:
        assert client.get("/api/v1/me", headers={"Authorization": "Bearer " + old_token}).status_code == 401
        linked = register(client, verification_mailbox, {**CREDENTIALS, "legacy_username": "old-student"})
        assert linked.status_code == 201, linked.text
        assert linked.json()["id"] == user
        profile = client.get("/api/v1/me", headers=sign_in(client)).json()["profile"]
        assert profile["student_number"] == PROFILE["student_number"]


def test_browser_session_works_in_new_tab_and_requires_same_origin_to_write(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        assert register(client, verification_mailbox).status_code == 201
        same_origin = {"Origin": "http://testserver"}
        login = client.post("/api/v1/auth/login", headers=same_origin, json=CREDENTIALS)
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert client.get("/api/v1/me").status_code == 200
        assert client.put("/api/v1/me/profile", headers={"Origin": "https://knuin.knu.ac.kr"}, json=PROFILE).status_code == 403
        assert client.put("/api/v1/me/profile", headers=same_origin, json=PROFILE).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=same_origin).status_code == 204
        assert client.get("/api/v1/me").status_code == 401

