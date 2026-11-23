from fastapi.testclient import TestClient

from app.main import app

PROFILE = {"admission_year": 2024, "major": "aicom"}
PARAMS = {"admission_year": 2024, "major": "aicom"}


def test_requirements_month_and_tracks():
    with TestClient(app) as client:
        assert client.get("/requirements").status_code == 200
        tracks = client.get("/api/v1/requirements/tracks", params=PARAMS)
        assert tracks.json()["tracks"] == []
        assert tracks.json()["allows_no_track"] is True
        for month in ("2027-02", "2027-08", "2027-03", "2027-08-01"):
            result = client.get("/api/v1/requirements", params={**PARAMS, "expected_graduation_date": month})
            assert result.status_code == (200 if month in ("2027-02", "2027-08") else 422)


def test_imported_rules_are_available_in_default_app():
    with TestClient(app) as client:
        response = client.get("/api/v1/requirements", params=PARAMS)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["id"] == "aicom_2024-2025"
        assert {item["area"]: item["minimum"] for item in result["credit_requirements"]} == {
            "total": 140, "major": 72, "liberal_arts": 30,
        }
        assert len(result["course_requirements"][0]["courses"]) == 7
        assert result["equivalent_courses"]["COMP0453"] == ["COMP0333"]
        assert client.get("/api/v1/requirements", params={**PARAMS, "admission_year": 2025}).status_code == 200
        assert client.get("/api/v1/requirements", params={**PARAMS, "transfer_student": True}).status_code == 422


def test_imported_preview_checks_offering_classification_and_caps():
    def course(code, category, offered=None):
        return {
            "course_code": code, "transcript_category": category,
            "offered_by_computer_science": offered, "credits": 3,
            "status": "completed", "semester": "2024-1",
        }

    courses = [
        course("COMP0333", "전공필수", True),
        course("COMP0454", "전공필수", False),
        course("COME0331", "전공필수"),
        course("COMP0319", "일반선택", True),
        course("CLTR0000", "교양"),
        *(course(f"FUTR999{i}", "일반선택") for i in range(5)),
    ]
    with TestClient(app) as client:
        response = client.post("/api/v1/evaluations/preview", json={
            "student_profile": PROFILE, "completed_courses": courses,
        })
    assert response.status_code == 200, response.text
    result = response.json()
    requirements = {item["id"]: item for item in result["requirements"]}
    assert result["eligible"] is False
    assert requirements["total"]["current"] == 27
    assert requirements["total"]["shortfall"] == 113
    assert requirements["major"]["current"] == 3
    assert requirements["liberal"]["current"] == 3
    required = {item["course_code"]: item["status"] for item in requirements["required-major"]["required_courses"]}
    assert required["COMP0453"] == "completed"
    assert required["COMP0454"] == "missing"
    assert required["COME0331"] == "verification_required"
    assert required["COMP0319"] == "approval_required"
    assert requirements["NC-CURR-AICOM-2024"]["status"] == "verification_required"
    assert sum(item["excluded_credits"] for item in result["credit_entries"]) == 3
