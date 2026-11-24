import json

import httpx
from fastapi.testclient import TestClient

from app.services import ai_client
from tests.test_accounts_api import CREDENTIALS, PROFILE, create_demo_app, register, sign_in


def test_chat_uses_authenticated_student_evaluation_and_verified_sources(
    monkeypatch, tmp_path, verification_mailbox, caplog,
):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = "/api/v1/me/graduation-chat"
    question = {"question": "test0123 과목은 어떻게 인정됐고 졸업 제출 기한은 언제예요?"}

    with TestClient(create_demo_app()) as client:
        assert client.post(path, json=question).status_code == 401
        assert register(client, verification_mailbox).status_code == 201
        headers = sign_in(client)
        assert client.post(path, headers=headers, json=question).status_code == 409
        assert client.put("/api/v1/me/profile", headers=headers, json=PROFILE).status_code == 200
        assert client.post(path, headers=headers, json={"question": " "}).status_code == 422
        records = {"completed_courses": [
            {"course_code": code, "course_name": name, "semester": "2024-1", "credits": 3,
             "transcript_category": "전공", "status": "completed"}
            for code, name in (("TEST0123", "테스트 과목"), ("TEST0456", "별도 과목"))
        ]}
        assert client.put("/api/v1/me/records", headers=headers, json=records).status_code == 200
        assert client.post(path, headers=headers, json=question).status_code == 503
        assert "GEMINI_API_KEY (또는 OPENAI_API_KEY) is missing" in caplog.text
        evaluation = client.get("/api/v1/me/evaluation", headers=headers).json()
        first_source = next(item["sources"][0] for item in evaluation["requirements"] if item["sources"])

        def post(url, **kwargs):
            body = kwargs["json"]
            assert body["store"] is False
            assert body["model"] == "gpt-5.4-mini"
            assert PROFILE["student_number"] not in body["input"] and CREDENTIALS["email"] not in body["input"]
            chat_input = json.loads(body["input"])
            assert chat_input["question"] == question["question"]
            context = chat_input["evaluation"]
            assert "credit_entries" not in context
            assert context["rule_id"] == evaluation["rule_id"]
            assert context["requirements"][0]["shortfall"] == evaluation["requirements"][0]["shortfall"]
            assert chat_input["applicable_rule"]["id"] == evaluation["rule_id"]
            assert chat_input["applicable_rule"]["course_requirements"]
            assert chat_input["expected_graduation_date"] == PROFILE["expected_graduation_date"]
            schedule = chat_input["graduation_schedule"]
            assert schedule["department_deadline"]["source_ids"]
            assert all(event["graduation"] == PROFILE["expected_graduation_date"] for event in schedule["events"])
            assert all(item["graduation"] == PROFILE["expected_graduation_date"]
                       for item in schedule["qualification_deadlines"])
            assert chat_input["course_credit_evidence"] == [
                entry for entry in evaluation["credit_entries"] if entry["course_code"] == "TEST0123"
            ]
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "status": "completed", "output": [{"type": "message", "content": [{
                    "type": "output_text", "text": json.dumps({"answer": "테스트 답변", "source_ids": [0, 0, -1, 9999]}),
                }]}],
            })

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("JOLUP_OPENAI_CHAT_MODEL", " ")
        monkeypatch.setenv("JOLUP_OPENAI_MODEL", " gpt-5.4-mini ")
        monkeypatch.setattr(ai_client.httpx, "post", post)
        response = client.post(path, headers=headers, json=question)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "answer": "테스트 답변", "rule_id": evaluation["rule_id"], "sources": [first_source],
        }

        def quota_exhausted(url, **kwargs):
            return httpx.Response(429, request=httpx.Request("POST", url), json={
                "error": {"code": "insufficient_quota", "message": "private upstream details"},
            })

        monkeypatch.setattr(ai_client.httpx, "post", quota_exhausted)
        failure = client.post(path, headers=headers, json=question)
        assert failure.status_code == 503
        assert "openai HTTP 429 insufficient_quota" in caplog.text
        assert "private upstream details" not in caplog.text + failure.text


def test_chat_uses_gemini_when_its_key_is_set(monkeypatch, tmp_path, verification_mailbox, caplog):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    path = "/api/v1/me/graduation-chat"
    question = {"question": "전공학점이 얼마나 부족해요?"}
    with TestClient(create_demo_app()) as client:
        assert register(client, verification_mailbox).status_code == 201
        headers = sign_in(client)
        assert client.put("/api/v1/me/profile", headers=headers, json=PROFILE).status_code == 200
        evaluation = client.get("/api/v1/me/evaluation", headers=headers).json()
        first_source = next(item["sources"][0] for item in evaluation["requirements"] if item["sources"])

        def gemini(url, **kwargs):
            assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
            assert kwargs["headers"] == {"x-goog-api-key": "gemini-key"}
            body = kwargs["json"]
            assert "graduation requirements assistant" in body["systemInstruction"]["parts"][0]["text"]
            chat_input = json.loads(body["contents"][0]["parts"][0]["text"])
            assert chat_input["question"] == question["question"]
            assert PROFILE["student_number"] not in json.dumps(body, ensure_ascii=False)
            assert CREDENTIALS["email"] not in json.dumps(body, ensure_ascii=False)
            config = body["generationConfig"]
            assert config["responseMimeType"] == "application/json"
            assert config["responseSchema"] == {
                "type": "OBJECT", "propertyOrdering": ["answer", "source_ids"],
                "properties": {"answer": {"type": "STRING"},
                               "source_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
                "required": ["answer", "source_ids"],
            }
            return httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{
                "finishReason": "STOP", "content": {"role": "model", "parts": [
                    {"text": "생각 과정", "thought": True},
                    {"text": json.dumps({"answer": "제미나이 답변", "source_ids": [0]}, ensure_ascii=False)},
                ]},
            }]})

        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("OPENAI_API_KEY", "unused-openai-key")  # 둘 다 있으면 Gemini 우선
        monkeypatch.setattr(ai_client.httpx, "post", gemini)
        response = client.post(path, headers=headers, json=question)
        assert response.status_code == 200, response.text
        assert response.json() == {"answer": "제미나이 답변", "rule_id": evaluation["rule_id"], "sources": [first_source]}

        def cut_off(url, **kwargs):
            return httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{
                "finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{\"answer\": \"잘린"}]},
            }]})

        monkeypatch.setattr(ai_client.httpx, "post", cut_off)
        assert client.post(path, headers=headers, json=question).status_code == 503
        assert "gemini output token limit reached" in caplog.text

        def exhausted(url, **kwargs):
            return httpx.Response(429, request=httpx.Request("POST", url), json={"error": {
                "code": 429, "status": "RESOURCE_EXHAUSTED", "message": "private quota details",
            }})

        monkeypatch.setattr(ai_client.httpx, "post", exhausted)
        failure = client.post(path, headers=headers, json=question)
        assert failure.status_code == 503
        assert "gemini HTTP 429 RESOURCE_EXHAUSTED" in caplog.text
        assert "private quota details" not in caplog.text + failure.text
