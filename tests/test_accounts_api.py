import hashlib
import sqlite3

from fastapi.testclient import TestClient

from app.main import app
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


def test_english_test_details_survive_a_new_login(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    test = {"test_type": "OPIc", "raw_score": "IM2", "taken_month": "2026-11"}
    record = {"requirement_id": "department-english", "amount": 750, "status": "satisfied"}
    profile = {**PROFILE, "major": "glsop", "track": "multi_major", "registered_semesters": 6}
    with TestClient(app) as client:
        assert register(client, verification_mailbox).status_code == 201
        headers = sign_in(client)
        assert client.put("/api/v1/me/profile", headers=headers, json=profile).status_code == 200
        update = {"requirement_id": record["requirement_id"], "amount": record["amount"], "english_test": test}
        assert client.put("/api/v1/me/checklist", headers=headers, json=update).status_code == 200
        # An older client knows only the converted amount.
        assert client.put("/api/v1/me/records", headers=headers,
                          json={"nonacademic_records": [record]}).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    with TestClient(app) as client:
        headers = sign_in(client)
        assert client.get("/api/v1/me", headers=headers).json()["profile"]["registered_semesters"] == 6
        records = client.get("/api/v1/me/records", headers=headers).json()["nonacademic_records"]
        assert records == [{**record, "english_test": test}]
        evaluation = client.get("/api/v1/me/evaluation", headers=headers).json()
        english = next(item for item in evaluation["requirements"] if item["id"] == "department-english")
        assert (english["current"], english["status"]) == (750, "satisfied")
        assert evaluation["early_graduation"]["registered_semesters"] == 6
        assert evaluation["early_graduation"]["status"] == "verification_required"


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
        assert saved["convergence_program"] is None
        catalog = client.get("/api/v1/convergence-programs")
        assert catalog.status_code == 200
        assert any(item["id"] == "big-data" for item in catalog.json())
        assert all(set(item) == {"id", "name", "kind", "source", "courses"} and item["kind"] == "convergence"
                   for item in catalog.json())
        programs = {item["id"]: item for item in catalog.json()}
        local_categories = {course["code"]: course["curriculum_category"]
                            for course in programs["local-synergy"]["courses"]}
        assert local_categories["CHIL0364"] == "전공"
        assert local_categories["LCCN0205"] == "융합전공"
        assert all(course["curriculum_category"] == "전공" for course in programs["cognitive-science"]["courses"])
        invalid = client.put("/api/v1/me/profile", json={**PROFILE, "convergence_program": "unknown"}, headers=headers)
        assert invalid.status_code == 422
        response = client.put("/api/v1/me/profile", json={**PROFILE, "convergence_program": "big-data"}, headers=headers)
        assert response.status_code == 200, response.text
        saved = response.json()["profile"]
        assert saved["convergence_program"] == "big-data"
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
        assert client.get("/api/v1/me", headers=headers).json()["profile"]["convergence_program"] == "big-data"
        cleared = client.put("/api/v1/me/profile", json={**PROFILE, "convergence_program": None}, headers=headers)
        assert cleared.status_code == 200
        assert client.get("/api/v1/me", headers=headers).json()["profile"]["convergence_program"] is None
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



def request_reset(client, email):
    return client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "reset"})


def test_password_reset_changes_password_and_ends_old_sessions(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        assert register(client, verification_mailbox).status_code == 201
        old_session = sign_in(client)
        verification_mailbox.clear()

        assert request_reset(client, CREDENTIALS["email"]).status_code == 204
        code = verification_mailbox[CREDENTIALS["email"]]
        new_password = "brand-new-password"
        wrong = "000000" if code != "000000" else "000001"
        assert client.post("/api/v1/auth/password-reset", json={
            **CREDENTIALS, "password": new_password, "code": wrong,
        }).status_code == 400
        reset = client.post("/api/v1/auth/password-reset", json={
            **CREDENTIALS, "password": new_password, "code": code,
        })
        assert reset.status_code == 200, reset.text
        assert reset.headers["cache-control"] == "no-store"
        assert "password" not in reset.text

        # 바로 로그인된 새 세션, 기존 세션은 끊김
        new_session = {"Authorization": "Bearer " + reset.json()["access_token"]}
        assert client.get("/api/v1/me", headers=new_session).status_code == 200
        assert client.get("/api/v1/me", headers=old_session).status_code == 401
        assert client.post("/api/v1/auth/login", json=CREDENTIALS).status_code == 401
        sign_in(client, {**CREDENTIALS, "password": new_password})
        # 인증번호는 한 번만 쓸 수 있다
        assert client.post("/api/v1/auth/password-reset", json={
            **CREDENTIALS, "password": "another-password", "code": code,
        }).status_code == 400


def test_password_reset_does_not_reveal_whether_an_email_is_registered(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        assert register(client, verification_mailbox).status_code == 201
        verification_mailbox.clear()
        unknown = "nobody@knu.ac.kr"
        # 가입 여부와 관계없이 같은 응답, 같은 재요청 제한
        for email in (CREDENTIALS["email"], unknown):
            first, again = request_reset(client, email), request_reset(client, email)
            assert (first.status_code, first.content, again.status_code) == (204, b"", 429)
            assert again.json() == {"detail": "인증번호 요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."}
        # 가입되지 않은 이메일로는 메일을 보내지 않고, 재설정도 되지 않는다
        assert unknown not in verification_mailbox
        assert client.post("/api/v1/auth/password-reset", json={
            "email": unknown, "password": "whatever-password", "code": "123456",
        }).status_code == 400


def test_signup_and_reset_codes_are_not_interchangeable(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        email = CREDENTIALS["email"]
        assert client.post("/api/v1/auth/email-code", json={"email": email}).status_code == 204
        signup_code = verification_mailbox[email]
        # 가입용 인증번호로는 비밀번호를 바꿀 수 없다 (아직 계정도 없음)
        assert client.post("/api/v1/auth/password-reset", json={**CREDENTIALS, "code": signup_code}).status_code == 400
        assert client.post("/api/v1/auth/register", json={**CREDENTIALS, "code": signup_code}).status_code == 201

        get_account_store.cache_clear()
        store = get_account_store(tmp_path / "accounts.sqlite3")
        with store.connect() as conn:  # 재요청 제한(60초)을 지난 것으로 만든다
            conn.execute("UPDATE registration_codes SET sent_at = sent_at - 120")
        assert request_reset(client, email).status_code == 204
        reset_code = verification_mailbox[email]
        # 재설정용 인증번호로는 가입할 수 없다
        assert client.post("/api/v1/auth/register", json={
            **CREDENTIALS, "email": email, "code": reset_code,
        }).status_code == 400
        # 틀린 인증번호를 5번 넣으면 맞는 번호도 막힌다
        wrong = "000000" if reset_code != "000000" else "000001"
        for _ in range(5):
            assert client.post("/api/v1/auth/password-reset", json={**CREDENTIALS, "code": wrong}).status_code == 400
        assert client.post("/api/v1/auth/password-reset", json={**CREDENTIALS, "code": reset_code}).status_code == 400


def test_existing_code_table_gets_purpose_column(tmp_path):
    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE registration_codes (email TEXT PRIMARY KEY, nonce TEXT NOT NULL,
            digest BLOB NOT NULL, issuer TEXT NOT NULL, sent_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("INSERT INTO registration_codes VALUES ('a@knu.ac.kr', 'n', x'00', 'ip', 1, 0)")
    get_account_store.cache_clear()
    store = get_account_store(db)
    with store.connect() as conn:
        assert conn.execute("SELECT purpose FROM registration_codes").fetchone()[0] == "register"
