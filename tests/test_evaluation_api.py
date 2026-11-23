import pytest
from fastapi.testclient import TestClient

from app.api import requirements
from app.main import app
from tests.demo_app import create_demo_app


@pytest.fixture
def client():
    with TestClient(create_demo_app()) as client:
        yield client


def test_existing_routes_and_docs(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    assert "/api/v1/evaluations/preview" in client.get("/openapi.json").json()["paths"]


def test_majors_and_requirements(client):
    majors = client.get("/api/v1/majors", params={"admission_year": 2024}).json()["majors"]
    assert [major["code"] for major in majors] == ["glsop", "plsop", "aicom"]
    params = {"admission_year": 2024, "major": "aicom", "track": "test-track"}
    response = client.get("/api/v1/requirements", params=params)
    assert response.status_code == 200
    assert response.json()["dataset"] == "test"
    assert client.get("/api/v1/requirements", params={**params, "track": "bad"}).status_code == 422


def test_preview(client, student, course):
    response = client.post("/api/v1/evaluations/preview", json={
        "student_profile": student.model_dump(),
        "completed_courses": [item.model_dump() for item in (
            course(), course("TEST002"), course("TESTLIB", category="liberal_arts"),
        )],
        "nonacademic_records": [{"requirement_id": "test-english", "amount": 1, "status": "satisfied"}],
    })
    assert response.status_code == 200
    result = response.json()
    assert result["dataset"] == "test"
    assert result["eligible"] is True
    assert result["progress"] == 100
    assert all(item["sources"] for item in result["requirements"])


@pytest.mark.parametrize(("file_content", "code"), [
    (None, "rules_unavailable"),
    ("{", "invalid_rule_data"),
])
def test_default_api_rule_errors(monkeypatch, tmp_path, file_content, code):
    monkeypatch.setattr(requirements, "RULE_DIRECTORY", tmp_path)
    if file_content is not None:
        (tmp_path / "invalid.json").write_text(file_content, encoding="utf-8")
    with TestClient(app) as client:
        params = {"admission_year": 2024, "major": "aicom"}
        response = client.get("/api/v1/requirements", params=params)
        assert response.status_code == 503
        assert response.json()["code"] == code
        assert client.get("/api/v1/requirements", params={**params, "admission_year": 2021}).status_code == 422
