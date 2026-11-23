import csv
import re

import pytest

from app.schemas.course import Course
from app.schemas.rules import NonacademicRequirement
from app.services.graduation_engine import course_areas
from app.services.graduation_engine.course_areas import (
    DATA_DIRECTORY, abeek_area, abeek_result, auto_satisfied, design_result, liberal_result,
    sdg_result,
)

SOURCE = [{"title": "test"}]


def requirement(key, minimum=3, **extra):
    return NonacademicRequirement(id=key, name=key, minimum=minimum, sources=SOURCE, **extra)


def done(code, credits=3, category="교양", prior=None, semester="2024-1"):
    return Course(course_code=code, credits=credits, transcript_category=category, semester=semester,
                  status="completed", prior_institution=prior)


def test_portal_summary_replaces_course_mapping_for_required_liberal_areas():
    summary = {"독서와토론": 0, "사고교육": 0, "글쓰기": 3, "외국어": 0, "수리": 3,
               "기초과학": 0, "인문·사회": 6, "자연과학": 3}
    for key, credits in (("foundation_a", 3), ("foundation_b", 3), ("core_humanities", 6), ("core_science", 3)):
        evaluation = liberal_result(requirement(key), [done("TEST0999", 9)], summary)
        assert (evaluation.status, evaluation.current) == ("satisfied", credits)
    evaluation = liberal_result(requirement("core_science"), [], {**summary, "자연과학": 0})
    assert (evaluation.status, evaluation.current) == ("unsatisfied", 0)


@pytest.mark.parametrize("name", ["liberal_areas.csv", "startup_courses.csv"])
def test_mapping_files_are_well_formed_and_sourced(name):
    with open(DATA_DIRECTORY / name, encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert rows
    for row in rows:
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", row["code"]), row
        assert row["source"].strip(), row
    if name == "liberal_areas.csv":
        assert len(rows) >= 300  # 공식 이수영역별 교양과목 목록(2025)
        assert sum(row["sdg"] == "true" for row in rows) == 72  # 2025 목록 + 2024.5.1. SDG 목록


def test_renamed_courses_follow_the_admission_year_table():
    # 2021학번: 미래산업과 직업선택은 CLTR0689, 이후 FUTR0208로 코드만 변경
    assert abeek_area("CLTR0689", 2021) == ("basic", None)
    assert abeek_area("FUTR0208", 2021) == ("basic", "CLTR0689")
    assert abeek_area("CLTR0263", 2021) == ("basic", None)
    assert abeek_area("ENGR0211", 2021) == ("msc", None)
    assert abeek_area("COMP0454", 2022) == ("msc", None)
    # 2025 표에서 빠진 과목은 다른 학년도 표로 인정하지 않는다
    assert abeek_area("CLTR0264", 2024) == ("basic", None)
    assert abeek_area("CLTR0264", 2025) == (None, None)
    assert abeek_area("COMP0226", 2025) == ("major", None)
    assert abeek_area("ABCD0000", 2023) == (None, None)


def test_abeek_area_credits():
    courses = [done("CLTR0045"), done("CLTR0205"), done("FUTR0208"), done("COME0301", category="전공")]
    basic = abeek_result(requirement("abeek-basic_literacy", 9), courses, 2021)
    assert basic.status == "satisfied" and basic.current == 9
    assert any("FUTR0208(= CLTR0689)" in line for line in basic.details)
    msc = abeek_result(requirement("abeek-msc", 18), courses, 2021)
    assert msc.status == "unsatisfied" and msc.current == 3
    # 학부 사이트에 표가 없는 학번은 추정이므로 확인 필요
    assert abeek_result(requirement("abeek-msc", 18), courses, 2018).status == "verification_required"


def test_design_credits_come_from_rule():
    rule = requirement("abeek-design_min", 14, course_credits={"COMP0205": 2, "ITEC0401": 4})
    result = design_result(rule, [done("COMP0205"), done("ITEC0401", 4), done("COMP0312")])
    assert result.current == 6 and result.status == "unsatisfied"


def test_liberal_area_mapping_transcript_label_and_unknown_courses():
    writing = requirement("foundation_a")
    assert liberal_result(writing, [done("CLTR0205")]).status == "satisfied"
    # 매핑표에 없지만 성적표 교과구분에 영역이 적혀 있으면 사용
    labeled = done("CLTR9990", category="교양(첨성인기초-글쓰기)")
    assert liberal_result(writing, [labeled]).status == "satisfied"
    # 영역을 모르는 교양이 있으면 미충족으로 단정하지 않는다
    unknown = liberal_result(requirement("core_science"), [done("CLTR0999")])
    assert unknown.status == "verification_required"
    assert any("CLTR0999" in line for line in unknown.details)
    assert liberal_result(requirement("core_science"), [done("COMP0312", category="전공")]).status == "unsatisfied"
    # 전적대 과목은 영역 필수과목으로 인정하지 않는다
    assert liberal_result(writing, [done("CLTR0205", prior=True)]).status == "unsatisfied"


def test_sdg_uses_official_list():
    rule = requirement("sdg")
    assert sdg_result(rule, [done("CLTR0043")]).status == "satisfied"
    assert sdg_result(rule, [done("CLTR0205")]).status == "unsatisfied"


def test_software_is_confirmed_by_substitute_course_or_sw_area():
    software = requirement("software", 1, course_codes=["COME0331", "COMP0217"])
    assert "COME0331" in auto_satisfied(software, [done("COME0331", category="전공필수")], {})
    assert "첨성인기초(소프트웨어)" in auto_satisfied(software, [done("CLTR0264")], {})
    assert auto_satisfied(software, [done("CLTR0205")], {}) is None
    assert auto_satisfied(requirement("counseling", 8), [done("COME0331")], {}) is None


def test_computed_requirements_leave_the_checklist():
    from app.services.graduation_engine.evaluator import checklist_requirements

    class Rule:
        nonacademic_requirements = [requirement(key) for key in
                                    ("gpa", "sdg", "foundation_a", "abeek-msc", "software", "counseling")]

    assert [item.id for item in checklist_requirements(Rule())] == ["software", "counseling"]
    assert course_areas.COMPUTED_IDS >= {"sdg", "foundation_a", "abeek-msc", "abeek-design_min"}


def test_practical_english_by_area_credits():
    english = requirement("practical-english", 1)
    codes = [row["code"] for row in csv.DictReader(open(DATA_DIRECTORY / "liberal_areas.csv", encoding="utf-8"))
             if row["area"] == "실용영어"]
    assert codes
    assert auto_satisfied(english, [done(codes[0], credits=2)], {}) is None
    assert "실용영어" in auto_satisfied(english, [done(codes[0], credits=4)], {})
    # TOPIK 대체(4급)는 과목으로 충족되지 않는다
    assert auto_satisfied(requirement("practical-english", 4), [done(codes[0], credits=4)], {}) is None


def test_startup_credits_and_15_credit_substitute():
    from app.services.graduation_engine.course_areas import startup_result

    courses = [done("GLSO0223", category="전공"), done("FUTR0201", category="일반선택"),
               Course(course_code="ABCD0001", course_name="로컬 창업 실습", credits=3, transcript_category="일반선택",
                      semester="2024-1", status="completed")]
    credits = startup_result(requirement("track-startup_credits", 9), courses)
    assert credits.status == "satisfied" and credits.current == 9
    activity = requirement("track-startup_activity", 1)
    assert auto_satisfied(activity, courses, {}) is None
    more = courses + [done("CLTR0089"), done("FUTR0214")]
    assert "15학점" in auto_satisfied(activity, more, {})


def test_glsop_major_table_follows_admission_year():
    from app.services.graduation_engine.course_areas import glsop_major_courses

    assert "GLSO0218" in glsop_major_courses(2021)  # SW융합설계1: 2020~2021 교육과정
    assert "ITEC0402" in glsop_major_courses(2022)
    assert "ITEC0402" not in glsop_major_courses(2023)  # 2023 개편으로 삭제, 2022학번까지만
    assert "MTED0231" not in glsop_major_courses(2023)  # 심컴 교육과정 과목은 글솝 전공 불인정
