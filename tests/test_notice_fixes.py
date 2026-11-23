"""접수된 문제(공지 출처, 종합설계, 다전공 학점, 체크리스트 수치)의 회귀 테스트. 실제 규칙 묶음을 사용한다."""

import pytest
from fastapi.testclient import TestClient

from app.api.requirements import load_rules_from_default_directory
from app.main import app
from app.schemas.course import Course
from app.schemas.student import StudentProfile
from app.services.graduation_engine.progress import build_progress
from app.services.graduation_engine.rule_selector import select_rules

PLSOP_ONLY = {"S10", "S22", "S23", "S24", "S30"}
AICOM_ONLY = {"S1", "S2", "S3", "S4", "S5", "S6", "S27"}
GLSOP_ONLY = {"S16", "S17", "S18", "S19", "S20", "S28", "S29", "S33"}
TITLES = {}


@pytest.fixture(scope="module")
def rules():
    source = load_rules_from_default_directory()
    from jolup_rules_all.app.rules import loader
    for item in loader.load_common("sources")["sources"]:
        TITLES[item["title"]] = item["id"]
    return source


def source_ids(rule):
    ids = set()
    for group in (rule.credit_requirements, rule.course_requirements, rule.nonacademic_requirements,
                  rule.verification_requirements):
        for item in group:
            ids.update(TITLES.get(source.title, source.title) for source in item.sources)
    return ids | {TITLES.get(source.title, source.title) for source in rule.sources}


@pytest.mark.parametrize(("profile", "foreign"), [
    (dict(admission_year=2023, major="glsop", track="multi_major"), PLSOP_ONLY | AICOM_ONLY),
    (dict(admission_year=2021, major="glsop", track="multi_major"), PLSOP_ONLY | AICOM_ONLY),
    (dict(admission_year=2024, major="aicom"), PLSOP_ONLY | GLSOP_ONLY),
    (dict(admission_year=2021, major="plsop", abeek=False), AICOM_ONLY | GLSOP_ONLY),
])
def test_requirements_cite_only_the_students_own_major_notices(rules, profile, foreign):
    rule = select_rules(StudentProfile(**profile), rules)
    assert not source_ids(rule) & foreign


def course(code, category="전공", credits=3, name=None):
    return Course(course_code=code, course_name=name, credits=credits, transcript_category=category,
                  semester="2023-1", status="completed", offered_by_computer_science=True)


def test_glsop_sw_convergence_design_counts_for_capstone_and_major(rules):
    profile = StudentProfile(admission_year=2021, major="glsop", track="multi_major")
    progress = build_progress(profile, [course("GLSO0218")], [], rules)
    items = {item.id: item for item in progress.items}
    assert items["major"].current == 3 and items["major"].status != "verification_required"
    capstone = select_rules(StudentProfile(admission_year=2022, major="glsop", track="multi_major"), rules)
    options = next(item for item in capstone.course_requirements if item.id == "track-capstone").courses
    assert {"GLSO0218", "GLSO0221", "ITEC0401", "ITEC0402"} == {option.course_code for option in options}


@pytest.mark.parametrize("code", ["GLSO0218", "GLSO0221", "ITEC0401", "ITEC0402"])
def test_confirmed_capstones_correct_department_metadata(rules, code):
    profile = StudentProfile(admission_year=2023, major="glsop", track="multi_major")
    design = course(code).model_copy(update={"offered_by_computer_science": False})
    progress = build_progress(profile, [design], [], rules)
    assert progress.credit_entries[0].offered_by_computer_science is True
    assert next(item for item in progress.items if item.id == "track-capstone").checked is None
    assert next(item for item in progress.items if item.id == "track-capstone").status == "satisfied"
    if code == "ITEC0402":
        assert progress.credit_entries[0].recognized_category == "general"


def test_multi_major_credits_count_toward_total_not_major(rules):
    profile = StudentProfile(admission_year=2023, major="glsop", track="multi_major")
    progress = build_progress(profile, [course("COMP0312", "부전공"), course("ECON0101", "복수전공")], [], rules)
    items = {item.id: item for item in progress.items}
    assert items["total"].current == 6 and items["major"].current == 0


def test_startup_activity_is_satisfied_by_15_startup_credits(rules):
    profile = StudentProfile(admission_year=2023, major="glsop", track="multi_major")
    codes = ["GLSO0223", "GLSO0229", "COMP0428", "ITEC0403", "FUTR0208"]
    items = {item.id: item for item in build_progress(profile, [course(c) for c in codes], [], rules).items}
    assert items["track-startup_credits"].kind == "course" and items["track-startup_credits"].current == 15
    assert items["track-startup_activity"].auto_checked


def test_preview_uses_portal_summary_and_recognizes_internship_credits():
    profile = {"major": "glsop", "admission_year": 2023, "track": "multi_major"}
    internship = course("OVIN0213", category="일반선택", credits=2, name="해외인턴십5")
    practice = course("ITEC0999", category="현장실습", credits=1)
    excluded = course("STUP0311", category="현장실습", credits=3, name="창업현장실습")
    summary = {"독서와토론": 0, "사고교육": 0, "글쓰기": 3, "외국어": 0, "수리": 3,
               "기초과학": 0, "인문·사회": 6, "자연과학": 3}
    with TestClient(app) as client:
        def evaluate(*courses):
            response = client.post("/api/v1/evaluations/preview", json={
                "student_profile": profile, "liberal_summary": summary,
                "completed_courses": [course.model_dump() for course in courses],
            })
            assert response.status_code == 200, response.text
            return {item["id"]: item for item in response.json()["requirements"]}

        partial = evaluate(internship, internship, excluded)
        assert partial["foundation_a"]["current"] == 3
        assert partial["core_humanities"]["current"] == 6
        assert partial["field-practice"]["current"] == 2
        assert partial["field-practice"]["status"] == "unsatisfied"
        assert "STUP0311" not in partial["field-practice"]["reason"]
        assert evaluate(internship, practice, excluded)["field-practice"]["status"] == "satisfied"
