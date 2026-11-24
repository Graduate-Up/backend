from fastapi.testclient import TestClient

from app.main import create_app

DEMO = {"email": "jolup-demo@knu.ac.kr", "password": "demo-password-1"}


def test_demo_account_is_created_on_startup_with_sample_records(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("JOLUP_DEMO_EMAIL", DEMO["email"])
    monkeypatch.setenv("JOLUP_DEMO_PASSWORD", DEMO["password"])
    with TestClient(create_app()) as client:
        session = client.post("/api/v1/auth/login", json=DEMO)
        assert session.status_code == 200, session.text
        headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
        me = client.get("/api/v1/me", headers=headers).json()
        assert me["profile"]["major"] == "glsop" and me["profile"]["admission_year"] == 2024
        records = client.get("/api/v1/me/records", headers=headers).json()
        assert len(records["completed_courses"]) >= 30
        evaluation = client.get("/api/v1/me/evaluation", headers=headers)
        assert evaluation.status_code == 200, evaluation.text
        assert evaluation.json()["eligible"] is False  # 3학년이라 아직 졸업 전

        # 데모 계정은 재설정 메일을 보내지 않는다 (응답은 같은 204)
        assert client.post("/api/v1/auth/email-code",
                           json={"email": DEMO["email"], "purpose": "reset"}).status_code == 204
        assert DEMO["email"] not in verification_mailbox
        # 데모 사용자가 저장한 내용은 다시 시작해도 덮어쓰지 않는다
        assert client.put("/api/v1/me/records", headers=headers, json={"completed_courses": []}).status_code == 200

    with TestClient(create_app()) as client:
        session = client.post("/api/v1/auth/login", json=DEMO).json()
        headers = {"Authorization": f"Bearer {session['access_token']}"}
        assert client.get("/api/v1/me/records", headers=headers).json()["completed_courses"] == []


def test_no_demo_account_without_settings(monkeypatch, tmp_path, verification_mailbox, caplog):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("JOLUP_DEMO_EMAIL", "demo@gmail.com")
    monkeypatch.setenv("JOLUP_DEMO_PASSWORD", DEMO["password"])
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/auth/login", json=DEMO).status_code == 401
    assert "데모 계정을 만들지 않음" in caplog.text
