"""전공·학번별 교육과정 적재 검증 (data/curricula/curricula.csv)."""

import csv
import json

import pytest

from app.schemas.course import Course
from app.schemas.rules import NonacademicRequirement
from app.services.graduation_engine.course_areas import (
    CURRICULA_FILE, curricula, curriculum, curriculum_is_sourced, curriculum_year, liberal_area_of, liberal_result,
)
from jolup_rules_all.app.rules import loader

# (전공, 입학년도) → (적용 교육과정 학년도, 학번 표 존재 여부)
EXPECTED = {
    **{("plsop", year): (2021, year >= 2020) for year in range(2017, 2022)},
    ("plsop", 2022): (2022, True), ("plsop", 2023): (2023, True),
    ("plsop", 2024): (2024, True), ("plsop", 2025): (2025, True),
    **{("glsop", year): (2021, year >= 2020) for year in range(2017, 2022)},
    ("glsop", 2022): (2022, True), ("glsop", 2023): (2023, True),
    ("glsop", 2024): (2024, True), ("glsop", 2025): (2025, True),
}


@pytest.mark.parametrize(("major", "year"), sorted(EXPECTED))
def test_each_major_and_admission_year_uses_its_own_table(major, year):
    assert (curriculum_year(major, year), curriculum_is_sourced(major, year)) == EXPECTED[major, year]


def test_table_sizes_and_categories():
    counts = {}
    with open(CURRICULA_FILE, encoding="utf-8") as file:
        for row in csv.DictReader(file):
            key = (row["major"], int(row["curriculum_year"]), row["category"])
            counts[key] = counts.get(key, 0) + 1
            assert row["source"].strip()
    assert {key[2] for key in counts if key[0] == "plsop"} == {"기본소양", "전공기반", "공학전공", "교직"}
    assert {key[2] for key in counts if key[0] == "glsop"} <= {"교양", "전공", "일반선택"}
    assert counts["plsop", 2021, "공학전공"] == 47 and counts["plsop", 2022, "공학전공"] == 54
    assert counts["glsop", 2021, "전공"] == 46 and counts["glsop", 2022, "전공"] == 54
    assert counts["glsop", 2023, "전공"] == counts["glsop", 2025, "전공"] == 48


def test_plsop_year_differences():
    assert curriculum("plsop", 2025)["COMP0226"].category == "공학전공"  # 2025 신설 양자컴퓨팅개론
    assert "CLTR0264" not in curriculum("plsop", 2025)
    assert curriculum("plsop", 2024)["CLTR0264"].category == "기본소양"
    assert curriculum("plsop", 2023)["CLTR0264"].category == "기본소양"
    assert curriculum("plsop", 2022)["CLTR0732"].category == "기본소양"
    assert curriculum("plsop", 2021)["ENGR0211"].category == "전공기반"
    assert {code for code, course in curriculum("plsop", 2025).items() if course.category == "교직"} == {
        "TCHR0499", "TCHR0593", "TCHR0594", "TCHR0522"}


def test_plsop_design_flags_match_rule_design_courses():
    design = json.loads(
        (loader.RULES_DIR / "base/plsop_abeek_2023-2025.json").read_text(encoding="utf-8")
    )["credits"]["abeek"]
    for year, table in curricula()["plsop"].items():
        assert {code for code, course in table.items() if course.design} == set(design["design_courses"]), year


def test_glsop_tags_startup_and_liberal_division():
    assert set(curriculum("glsop", 2021)["CLTR0086"].tags) == {"첨성인일반", "창업"}
    assert "창업" in curriculum("glsop", 2025)["GLSO0223"].tags
    assert "첨성인기초(글쓰기)" in curriculum("glsop", 2023)["CLTR0205"].tags
    assert curriculum("glsop", 2021)["GLSO0218"].category == "전공"


def course(code, semester):
    return Course(course_code=code, credits=3, transcript_category="교양", semester=semester, status="completed")


def requirement(key):
    return NonacademicRequirement(id=key, name=key, minimum=3, sources=[{"title": "t"}])


def test_liberal_area_follows_the_semester_taken():
    # 중국어1: 2022년까지 첨성인핵심 인문사회, 2023 개편 후 첨성인기초 외국어
    assert (liberal_area_of(course("CLTR0011", "2022-1")).group, liberal_area_of(course("CLTR0011", "2022-1")).area) \
        == ("첨성인핵심", "인문·사회")
    assert liberal_area_of(course("CLTR0011", "2024-1")).group == "첨성인기초"
    assert liberal_result(requirement("foundation_a"), [course("CLTR0011", "2024-1")]).status == "satisfied"
    assert liberal_result(requirement("foundation_a"), [course("CLTR0011", "2022-1")]).status == "unsatisfied"
    # 수학 I은 2023 이후 첨성인핵심 자연과학: 첨성인기초 수리로 세지 않는다
    assert liberal_result(requirement("core_science"), [course("CLTR0211", "2024-1")]).status == "satisfied"
    assert liberal_result(requirement("foundation_b"), [course("CLTR0211", "2024-1")]).status == "unsatisfied"
    # 실용화법은 2023 이후 첨성인소양: 인문·사회 핵심이 아니다
    assert liberal_result(requirement("core_humanities"), [course("CLTR0003", "2024-1")]).status == "unsatisfied"


@pytest.mark.parametrize(("year", "confirm"), [(2024, True), (2025, False)])
def test_aicom_2024_2025_recognise_all_major_courses(year, confirm):
    from app.api.requirements import load_rules_from_default_directory
    from app.schemas.student import StudentProfile
    from app.services.graduation_engine.rule_selector import select_rules

    rule = select_rules(StudentProfile(admission_year=year, major="aicom"), load_rules_from_default_directory())
    assert len(rule.department_major_codes) == 50
    assert any(item.id == "NC-CURR-AICOM-2024" for item in rule.verification_requirements) is confirm


def test_each_course_shows_its_area_for_the_major_and_admission_year():
    from app.services.graduation_engine.course_areas import course_labels

    # 플솝: 입학년도 표의 ABEEK 구분 + 필수·설계, 교양은 수강 학기 영역
    assert course_labels(course("CLTR0205", "2021-1"), "plsop", 2021) == ["기본소양", "첨성인기초(글쓰기)"]
    assert course_labels(course("COMP0205", "2022-1"), "plsop", 2021) == ["공학전공", "필수", "설계"]
    assert course_labels(course("ENGR0211", "2021-2"), "plsop", 2021) == ["전공기반"]
    assert course_labels(course("FUTR0208", "2022-1"), "plsop", 2021)[0] == "기본소양"  # CLTR0689 코드 변경
    assert "기본소양" not in course_labels(course("CLTR0264", "2025-1"), "plsop", 2025)  # 2025 표에서 빠짐
    teaching = {row["code"] for row in csv.DictReader(open(CURRICULA_FILE, encoding="utf-8"))
                if row["major"] == "plsop" and row["curriculum_year"] == "2023" and row["category"] == "교직"}
    assert teaching and all(course_labels(course(code, "2024-1"), "plsop", 2023)[0] == "교직" for code in teaching)
    # 글솝: 교육과정 구분 + 창업교과목, 교양 영역 4가지
    assert course_labels(course("GLSO0223", "2024-1"), "glsop", 2023) == ["전공", "창업교과목"]
    assert course_labels(course("CLTR0043", "2024-1"), "glsop", 2023) == ["교양", "첨성인핵심(인문·사회)", "SDG"]
    assert course_labels(course("CLTR0011", "2022-1"), "glsop", 2022)[-1] == "첨성인핵심(인문·사회)"
    assert course_labels(course("CLTR0011", "2024-1"), "glsop", 2023)[-1] == "첨성인기초(외국어)"
    assert course_labels(course("GLSO0218", "2024-1"), "glsop", 2023) == []  # 2023 표에서 삭제


def test_all_four_liberal_groups_are_loaded_by_semester():
    data = json.loads((CURRICULA_FILE.parent / "raw" / "cltr_areas.json").read_text(encoding="utf-8"))
    assert {item["area"][0] for item in data["courses"]} == {"첨성인기초", "첨성인핵심", "첨성인소양", "첨성인일반"}
    from app.services.graduation_engine.course_areas import course_labels

    assert course_labels(course("CLTR0069", "2020-1"), "glsop", 2020)[-1] == "첨성인일반"  # 인간과성
    assert course_labels(course("CLTR0180", "2024-1"), "glsop", 2023)[-1] == "첨성인소양(생활과건강)"  # 골프
    # 실용화법: 2023~2025 PDF는 '첨성인핵심(예술과 표현)'이나 수업시간표 공식 분류는 첨성인소양 예술과표현
    assert liberal_area_of(course("CLTR0003", "2024-1")).group == "첨성인소양"
    # 소양·일반 과목은 기초·핵심 영역 요건으로 세지 않는다
    assert liberal_result(requirement("core_humanities"), [course("CLTR0180", "2024-1")]).status == "unsatisfied"
