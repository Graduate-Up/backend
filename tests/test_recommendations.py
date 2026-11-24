import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_client, recommendations
from tests.test_accounts_api import register, sign_in


def test_recommendation_uses_saved_courses_and_falls_back_without_openai(monkeypatch, tmp_path, verification_mailbox):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    load_programs = recommendations.programs_catalog
    monkeypatch.setattr(recommendations, "programs_catalog", lambda: [])
    with TestClient(app) as client:
        path = "/api/v1/me/recommendations"
        assert client.post(path, json={}).status_code == 401
        assert register(client, verification_mailbox).status_code == 201
        headers = sign_in(client)
        profile = {"student_number": "2023000001", "admission_year": 2023, "major": "glsop", "track": "multi_major"}
        assert client.put("/api/v1/me/profile", headers=headers, json=profile).status_code == 200
        assert client.put("/api/v1/me/records", headers=headers, json={"completed_courses": [{
            "course_code": "COMP0204", "semester": "2023-1", "credits": 6, "transcript_category": "전공필수",
            "status": "completed",
        }, {
            "course_code": "COME0331", "semester": "2023-2", "credits": 3, "transcript_category": "전공필수",
            "status": "completed",
        }]}).status_code == 200
        response = client.post(path, headers=headers, json={"career": "ai_data", "use_ai": True})
        monkeypatch.setattr(recommendations, "programs_catalog", lambda: [recommendations.Program(
            id="test-program", name="테스트 융합전공", kind="convergence",
            source={"title": "테스트 출처"}, courses=[
                {"code": "COMP0204", "name": "프로그래밍기초", "credits": 6},
                {"code": "TEST1234", "name": "테스트 응용과목", "credits": 3, "career_tags": ["ai_data"]},
            ],
        )])
        with_program = client.post(path, headers=headers, json={"career": "ai_data"})
        monkeypatch.setattr(recommendations, "programs_catalog", load_programs)
        with_uploaded_catalog = client.post(path, headers=headers, json={"career": "ai_data"})

        def rank_candidates(url, **kwargs):
            candidates = json.loads(kwargs["json"]["input"])["candidates"]
            assert len(candidates) >= 2
            order = [item["id"] for item in reversed(candidates)]
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "status": "completed", "output": [{"type": "message", "content": [{
                    "type": "output_text", "text": json.dumps({"ids": order}),
                }]}],
            })

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(ai_client.httpx, "post", rank_candidates)
        with_ai = client.post(path, headers=headers, json={"career": "ai_data", "use_ai": True})
    assert response.status_code == 200, response.text
    data = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert data["ranking"] == "rules" and data["inferred"] is False
    assert data["curriculum_source"]["url"].endswith("/global_2023.pdf")
    assert len(data["recommended"]) == 3
    assert all(item["course_code"] != "COMP0204" for item in data["recommended"] + data["alternatives"])
    assert all(item["gains"] for item in data["recommended"])
    assert data["program_data_available"] is False
    assert with_program.status_code == 200, with_program.text
    assert with_program.json()["programs"][0]["overlapping_course_codes"] == ["COMP0204"]
    assert with_uploaded_catalog.status_code == 200, with_uploaded_catalog.text
    uploaded = with_uploaded_catalog.json()
    assert uploaded["program_data_available"] is True
    ai_politics = next(program for program in uploaded["programs"] if program["id"] == "ai-politics")
    assert ai_politics["overlapping_course_codes"] == ["COME0331"]
    assert with_ai.status_code == 200, with_ai.text
    assert with_ai.json()["ranking"] == "ai"
    assert with_ai.json()["recommended"][0]["course_code"] != uploaded["recommended"][0]["course_code"]


def test_ai_ranking_accepts_only_candidate_permutations(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    candidates = [recommendations.CourseRecommendation(
        course_code=code, course_name=name, credits=3, curriculum_category="전공",
        career_tags=["ai_data"], gains=[], reason="test",
    ) for code, name in (("ITEC0419", "데이터과학기초"), ("ITEC0417", "기계학습개론"))]
    order = ["ITEC0417", "ITEC0419"]

    def post(url, **kwargs):
        assert url.endswith("/v1/responses")
        assert kwargs["json"]["store"] is False
        assert set(json.loads(kwargs["json"]["input"])) == {"career", "interest_tags", "candidates"}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "status": "completed", "output": [{"type": "message", "content": [{
                "type": "output_text", "text": json.dumps({"ids": order}),
            }]}],
        })

    monkeypatch.setattr(ai_client.httpx, "post", post)
    assert recommendations.ai_order(candidates, "ai_data", {"ai_data": 1}) == order
    order[0] = "FAKE0001"
    assert recommendations.ai_order(candidates, "ai_data", {}) == []
    assert "no valid candidate order" in caplog.text

    def rate_limited(url, **kwargs):
        return httpx.Response(429, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_client.httpx, "post", rate_limited)
    assert recommendations.ai_order(candidates, "ai_data", {}) == []
    assert "HTTP 429" in caplog.text


def test_ai_ranking_with_gemini(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("JOLUP_GEMINI_MODEL", "gemini-test-model")
    candidates = [recommendations.CourseRecommendation(
        course_code=code, course_name=name, credits=3, curriculum_category="전공",
        career_tags=["ai_data"], gains=[], reason="test",
    ) for code, name in (("ITEC0419", "데이터과학기초"), ("ITEC0417", "기계학습개론"))]

    def post(url, **kwargs):
        assert url.endswith("/models/gemini-test-model:generateContent")
        sent = json.loads(kwargs["json"]["contents"][0]["parts"][0]["text"])
        assert [item["id"] for item in sent["candidates"]] == ["ITEC0419", "ITEC0417"]
        return httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{
            "finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"ids": ["ITEC0417", "ITEC0419"]})}]},
        }]})

    monkeypatch.setattr(ai_client.httpx, "post", post)
    assert recommendations.ai_order(candidates, "ai_data", {}) == ["ITEC0417", "ITEC0419"]

    monkeypatch.delenv("GEMINI_API_KEY")
    assert recommendations.ai_order(candidates, "ai_data", {}) == []
    assert "AI recommendation fallback: GEMINI_API_KEY (또는 OPENAI_API_KEY) is missing" in caplog.text
