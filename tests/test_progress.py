import pytest
from fastapi.testclient import TestClient

from app.schemas.evaluation import NonacademicRecord
from app.schemas.rules import NonacademicRequirement
from app.services.graduation_engine.progress import build_progress, toggle_checklist
from app.services.graduation_engine.rule_selector import select_rules
from tests.demo_app import create_demo_app
from tests.test_accounts_api import CREDENTIALS, PROFILE, sign_in


def test_only_course_requirements_are_calculated_and_checklist_follows_records(rules, student, course):
    courses = [course("TEST001", 3, "major"), course("TESTLIB", 3, "liberal_arts")]

    result = build_progress(student, courses, [], rules)
    kinds = {item.id: item.kind for item in result.items}
    assert kinds["total"] == kinds["major"] == kinds["core"] == "course"
    assert kinds["test-english"] == "checklist"
    english = next(item for item in result.items if item.id == "test-english")
    assert english.checked is False and english.fraction == 0
    assert all(item.checked is None for item in result.items if item.kind == "course")
    assert 0 < result.course_progress < 100
    assert result.progress < result.course_progress

    checked = [NonacademicRecord(requirement_id="test-english", amount=1, status="satisfied")]
    after = build_progress(student, courses, checked, rules)
    assert next(item for item in after.items if item.id == "test-english").checked is True
    assert after.progress > result.progress
    assert after.course_progress == result.course_progress


def test_toggle_checklist_replaces_only_that_requirement(rules, student):
    rule = select_rules(student, rules)
    other = NonacademicRecord(requirement_id="something-else", amount=2, status="satisfied")
    on = toggle_checklist(rule, [other], "test-english", True)
    assert [record.requirement_id for record in on] == ["something-else", "test-english"]
    assert toggle_checklist(rule, on, "test-english", False) == [other]


def test_progress_api_and_checklist_toggle(monkeypatch, tmp_path):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        assert client.get("/dashboard-view.js").status_code == 200
        assert client.post("/api/v1/auth/register", json=CREDENTIALS).status_code == 201
        headers = sign_in(client)
        assert client.get("/api/v1/me/progress", headers=headers).status_code == 409
        assert client.put("/api/v1/me/profile", json=PROFILE, headers=headers).status_code == 200
        course = {"course_code": "TEST001", "semester": "2024-1", "credits": 3,
                  "transcript_category": "major", "status": "completed"}
        client.put("/api/v1/me/records", headers=headers, json={"completed_courses": [course]})

        before = client.get("/api/v1/me/progress", headers=headers).json()
        assert 0 < before["progress"] < 100

        bad = client.put("/api/v1/me/checklist", headers=headers, json={"requirement_id": "nope", "checked": True})
        assert bad.status_code == 422

        after = client.put("/api/v1/me/checklist", headers=headers,
                           json={"requirement_id": "test-english", "checked": True}).json()
        assert after["progress"] > before["progress"]
        assert after["course_progress"] == before["course_progress"]
        records = client.get("/api/v1/me/records", headers=headers).json()
        assert records["completed_courses"][0]["course_code"] == "TEST001"
        assert records["nonacademic_records"][0]["requirement_id"] == "test-english"

        cleared = client.put("/api/v1/me/checklist", headers=headers,
                             json={"requirement_id": "test-english", "checked": False}).json()
        assert cleared["progress"] == before["progress"]


def test_import_keeps_grades_failed_courses_and_fills_missing_grades(monkeypatch, tmp_path):
    monkeypatch.setenv("JOLUP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(create_demo_app()) as client:
        assert client.post("/api/v1/auth/register", json=CREDENTIALS).status_code == 201
        headers = sign_in(client)
        assert client.put("/api/v1/me/profile", json=PROFILE, headers=headers).status_code == 200
        ungraded = {"course_code": "TEST0001", "semester": "2024-1", "credits": 3,
                    "transcript_category": "major", "status": "completed"}
        client.put("/api/v1/me/records", headers=headers, json={"completed_courses": [ungraded]})
        rows = ('{"data":[{"sbjetCd":"TEST0001","crdit":3,"year":2024,"semester":1,"grade":"A0","category":"major"},'
                '{"sbjetCd":"TEST0009","crdit":3,"year":2024,"semester":2,"grade":"F","category":"major"}]}')
        result = client.post("/api/v1/me/transcript/import", headers=headers, json={"text": rows, "expected": 2}).json()
        assert result["added"] == 1 and result["grades_filled"] == 1
        saved = client.get("/api/v1/me/records", headers=headers).json()["completed_courses"]
        assert {(c["course_code"], c["grade"], c["status"]) for c in saved} == {
            ("TEST0001", "A0", "completed"), ("TEST0009", "F", "failed"),
        }


def test_gpa_requirement_becomes_course_item(rules, student, course):
    common = next(rule for rule in rules if rule.id == "test-common")
    common.nonacademic_requirements.append(NonacademicRequirement(
        id="gpa", name="평점평균", minimum=1.7, sources=[{"title": "test"}], details=["4.3 만점, F 포함"],
    ))
    graded = course("TEST001", 3, "major").model_copy(update={"grade": "B0"})
    result = build_progress(student, [graded], [], rules)
    gpa = next(item for item in result.items if item.id == "gpa")
    assert gpa.kind == "course" and gpa.status == "satisfied" and gpa.current == 3.0 and gpa.unit == "점"
    assert gpa.details[0] == "4.3 만점, F 포함"
    with pytest.raises(KeyError):
        toggle_checklist(select_rules(student, rules), [], "gpa", True)


def test_checklist_stores_minimum_when_checked_and_real_amount_when_given(rules, student):
    from app.services.graduation_engine.progress import is_checked

    rule = select_rules(student, rules)
    requirement = next(item for item in rule.nonacademic_requirements if item.id == "test-english")
    rule.nonacademic_requirements[rule.nonacademic_requirements.index(requirement)] = requirement.model_copy(
        update={"minimum": 700})
    requirement = requirement.model_copy(update={"minimum": 700})
    checked = toggle_checklist(rule, [], "test-english", True)
    assert checked[0].amount == 700 and is_checked(requirement, checked)
    low = toggle_checklist(rule, [], "test-english", None, 650)
    assert low[0].amount == 650 and not is_checked(requirement, low)
    assert is_checked(requirement, toggle_checklist(rule, low, "test-english", None, 750))


def test_checklist_update_needs_exactly_one_value():
    from pydantic import ValidationError

    from app.schemas.evaluation import ChecklistUpdate

    assert ChecklistUpdate(requirement_id="x", amount=750).amount == 750
    for payload in ({"requirement_id": "x"}, {"requirement_id": "x", "checked": True, "amount": 1}):
        with pytest.raises(ValidationError):
            ChecklistUpdate(**payload)
