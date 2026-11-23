"""이수 과목이 어느 영역에 속하는지로 판정하는 요건.

학생이 체크하던 ABEEK 영역(기본소양·전공기반·공학전공·설계), 교양 영역(첨성인기초·첨성인핵심),
SDG 교양, 창업교과목(글솝 기술창업역량)을 과목코드 매핑표로 계산하고, SW·실용영어 졸업자격인정과
스타트업 창업은 과목으로 충족이 확인되면 자동 체크한다. 매핑표는 data/course_classification/ 에 있고
행마다 출처(학부 교육과정, 공지 첨부 목록)를 기록한다.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.schemas.course import Course
from app.schemas.evaluation import RequirementResult
from app.schemas.rules import GraduationRule, NonacademicRequirement

from .gpa import semester_key

DATA_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "course_classification"

ABEEK_AREAS = {"abeek-basic_literacy": "basic", "abeek-msc": "msc", "abeek-engineering_major": "major"}
ABEEK_DESIGN_ID = "abeek-design_min"
LIBERAL_AREAS = {
    "foundation_a": {"독서와토론", "사고교육", "글쓰기", "외국어"},
    "foundation_b": {"수리", "기초과학"},
    "core_humanities": {"인문·사회"},
    "core_science": {"자연과학"},
}
SDG_ID = "sdg"
SOFTWARE_ID = "software"
PRACTICAL_ENGLISH_ID = "practical-english"
STARTUP_CREDITS_ID = "track-startup_credits"
STARTUP_ACTIVITY_ID = "track-startup_activity"
# 스타트업 창업 대체 ⓔ: 창업교과목 총 15학점 이상 (기술창업역량의 9학점과 통합 인정, 글솝 공지 28591)
STARTUP_ACTIVITY_CREDITS = 15
SOFTWARE_AREA = "소프트웨어"
PRACTICAL_ENGLISH_AREA = "실용영어"
# 이수 과목으로 계산하므로 체크리스트에서 빠지는 요건
COMPUTED_IDS = {*ABEEK_AREAS, ABEEK_DESIGN_ID, *LIBERAL_AREAS, SDG_ID, STARTUP_CREDITS_ID}

# 학부 사이트의 플솝(심화컴퓨터공학) 교육과정: 2020~2021, 2022, 2023(=2024·2025) 학년도 기준표
ABEEK_TABLE_YEARS = (2021, 2022, 2023)
ABEEK_FIRST_SOURCED_YEAR = 2020
# 학부 사이트의 글솝 교육과정: 2020~2021, 2022, 2023(=2024·2025) 학년도 기준표
GLSOP_TABLE_YEARS = (2021, 2022, 2023)
KNOWN_AREAS = {area for areas in LIBERAL_AREAS.values() for area in areas} | {SOFTWARE_AREA, PRACTICAL_ENGLISH_AREA}
AREA_ALIASES = {"SW": SOFTWARE_AREA, "소프트웨어": SOFTWARE_AREA, "인문사회": "인문·사회"}


@dataclass(frozen=True)
class LiberalArea:
    name: str
    group: str
    area: str
    sdg: bool


@lru_cache(maxsize=1)
def liberal_areas() -> dict[str, LiberalArea]:
    with open(DATA_DIRECTORY / "liberal_areas.csv", encoding="utf-8") as file:
        return {row["code"]: LiberalArea(row["name"], row["group"], row["area"], row["sdg"] == "true")
                for row in csv.DictReader(file)}


@lru_cache(maxsize=1)
def abeek_tables() -> dict[int, dict[str, tuple[str, str]]]:
    tables: dict[int, dict[str, tuple[str, str]]] = {}
    with open(DATA_DIRECTORY / "abeek_plsop.csv", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            tables.setdefault(int(row["curriculum_year"]), {})[row["code"]] = (row["name"], row["area"])
    return tables


@lru_cache(maxsize=1)
def glsop_tables() -> dict[int, dict[str, str]]:
    tables: dict[int, dict[str, str]] = {}
    with open(DATA_DIRECTORY / "glsop_major.csv", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            tables.setdefault(int(row["curriculum_year"]), {})[row["code"]] = row["name"]
    return tables


@lru_cache(maxsize=1)
def startup_courses() -> dict[str, str]:
    with open(DATA_DIRECTORY / "startup_courses.csv", encoding="utf-8") as file:
        return {row["code"]: row["name"] for row in csv.DictReader(file)}


def table_year(admission_year: int, years: tuple[int, ...]) -> int:
    """교육과정은 입학년도(학번) 기준으로 적용한다."""
    return max([year for year in years if year <= admission_year] or [years[0]])


def abeek_table_year(admission_year: int) -> int:
    return table_year(admission_year, ABEEK_TABLE_YEARS)


def known_course_name(code: str) -> str | None:
    """학부 교육과정표에 있는 과목명 (최신 학년도 우선)."""
    for tables in (glsop_tables(), {year: {k: v[0] for k, v in t.items()} for year, t in abeek_tables().items()}):
        for year in sorted(tables, reverse=True):
            if code in tables[year]:
                return tables[year][code]
    return None


def glsop_major_courses(admission_year: int) -> dict[str, str]:
    """글솝 전공으로 인정되는 과목: 본인 입학년도 기준 교육과정의 전공 교과목 (글솝 공지 28594)."""
    return glsop_tables()[table_year(admission_year, GLSOP_TABLE_YEARS)]


def abeek_area(code: str, admission_year: int) -> tuple[str | None, int | None]:
    """Return (area, table year). Codes missing from the student's table (renamed courses such as
    CLTR0689 → FUTR0208) are looked up in the nearest other year's table."""
    tables = abeek_tables()
    own = abeek_table_year(admission_year)
    for year in sorted(ABEEK_TABLE_YEARS, key=lambda year: (abs(year - own), -year)):
        if code in tables.get(year, {}):
            return tables[year][code][1], year
    return None, None


def transcript_area(course: Course) -> str | None:
    """성적표 교과구분에 영역이 적혀 있으면 사용한다. 예: '첨성인기초(글쓰기)'."""
    text = course.transcript_category.replace("•", "·").replace("ㆍ", "·").replace(" ", "")
    for alias, area in AREA_ALIASES.items():
        if alias in text:
            return area
    return next((area for area in sorted(KNOWN_AREAS, key=len, reverse=True) if area.replace(" ", "") in text), None)


def is_liberal(course: Course) -> bool:
    return course.course_code.startswith("CLTR") or "교양" in course.transcript_category


def completed_courses(courses: list[Course], equivalents: dict[str, str]) -> list[Course]:
    """완료 과목을 동일과목 기준으로 한 번씩만 (가장 최근 학기) 센다."""
    latest: dict[str, Course] = {}
    for course in courses:
        if course.status != "completed":
            continue
        code = equivalents.get(course.course_code, course.course_code)
        if code not in latest or semester_key(course.semester) >= semester_key(latest[code].semester):
            latest[code] = course
    return list(latest.values())


def describe(course: Course) -> str:
    name = f" {course.course_name}" if course.course_name else ""
    return f"{course.course_code}{name} {course.credits:g}학점"


def result(requirement: NonacademicRequirement, current: float, *, uncertain: list[str], reason: str,
           details: list[str]) -> RequirementResult:
    shortfall = max(0, requirement.minimum - current)
    if not shortfall:
        status = "satisfied"
    elif uncertain:
        status = "verification_required"
    else:
        status = "unsatisfied"
    return RequirementResult(
        id=requirement.id, name=requirement.name, status=status, current=current, required=requirement.minimum,
        shortfall=shortfall, reason=reason, sources=requirement.sources,
        details=details + (uncertain if shortfall else []),
    )


def abeek_result(requirement: NonacademicRequirement, courses: list[Course], admission_year: int) -> RequirementResult:
    area = ABEEK_AREAS[requirement.id]
    own = abeek_table_year(admission_year)
    counted, borrowed = [], []
    for course in courses:
        found, year = abeek_area(course.course_code, admission_year)
        if found == area:
            counted.append(course)
            if year != own:
                borrowed.append(f"{course.course_code}({year}학년도 표 기준)")
    current = sum(course.credits for course in counted)
    reason = f"플솝 {own}학년도 기준 교육과정의 ABEEK 영역 표로 계산"
    uncertain = []
    if admission_year < ABEEK_FIRST_SOURCED_YEAR:
        uncertain.append(f"{admission_year}학번 교육과정표가 학부 사이트에 없어 {own}학년도 표로 추정했어요")
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 과목 없음"]
    if borrowed:
        details.append("과목코드가 바뀐 과목은 다른 학년도 표로 인정: " + ", ".join(borrowed))
    return result(requirement, current, uncertain=uncertain, reason=reason, details=details)


def design_result(requirement: NonacademicRequirement, courses: list[Course]) -> RequirementResult:
    counted = [(course, requirement.course_credits[course.course_code]) for course in courses
               if course.course_code in requirement.course_credits]
    current = sum(amount for _, amount in counted)
    details = ["인정: " + ", ".join(f"{course.course_code} 설계 {amount:g}학점" for course, amount in counted)
               if counted else "인정된 설계 과목 없음"]
    return result(requirement, current, uncertain=[], reason="ABEEK 설계 과목별 설계학점 합계", details=details)


def liberal_area_of(course: Course) -> LiberalArea | None:
    mapped = liberal_areas().get(course.course_code)
    if mapped is not None:
        return mapped
    area = transcript_area(course)
    return LiberalArea(course.course_name or "", "", area, False) if area else None


def liberal_result(requirement: NonacademicRequirement, courses: list[Course]) -> RequirementResult:
    targets = LIBERAL_AREAS[requirement.id]
    counted, unknown = [], []
    for course in courses:
        if course.prior_institution or not is_liberal(course):
            continue  # 영역별 필수과목은 경북대 개설 과목만 인정
        area = liberal_area_of(course)
        if area is None:
            unknown.append(course)
        elif area.area in targets:
            counted.append(course)
    current = sum(course.credits for course in counted)
    uncertain = ["영역을 확인하지 못한 교양: " + ", ".join(map(describe, unknown))] if unknown else []
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 과목 없음"]
    reason = "교양 과목코드 → 영역 매핑표(SDG 공식 목록, 학부 교육과정 영역 표기)와 성적표 교과구분으로 계산"
    return result(requirement, current, uncertain=uncertain, reason=reason, details=details)


def sdg_result(requirement: NonacademicRequirement, courses: list[Course]) -> RequirementResult:
    counted = [course for course in courses
               if not course.prior_institution and (area := liberal_areas().get(course.course_code)) and area.sdg]
    current = sum(course.credits for course in counted)
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 SDG 과목 없음"]
    return result(requirement, current, uncertain=[], details=details,
                  reason="SDG 교양교과목 공식 목록(2024.5.1. 기준 59과목)으로 계산")


def is_startup_course(course: Course) -> bool:
    """창업교과목: 인정목록의 과목 또는 학내 개설 교과목 중 과목명에 '창업'이 포함된 과목 (글솝 공지 28591)."""
    return course.course_code in startup_courses() or "창업" in (course.course_name or "")


def startup_credit_courses(courses: list[Course]) -> list[Course]:
    return [course for course in courses if not course.prior_institution and is_startup_course(course)]


def startup_result(requirement: NonacademicRequirement, courses: list[Course]) -> RequirementResult:
    counted = startup_credit_courses(courses)
    current = sum(course.credits for course in counted)
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 창업교과목 없음"]
    return result(requirement, current, uncertain=[], details=details,
                  reason="창업교과목 인정목록(2023~2026)과 과목명 '창업' 포함 여부로 계산")


def area_results(rule: GraduationRule, courses: list[Course], equivalents: dict[str, str],
                 admission_year: int) -> list[RequirementResult]:
    done = completed_courses(courses, equivalents)
    results = []
    for requirement in rule.nonacademic_requirements:
        if requirement.id in ABEEK_AREAS:
            results.append(abeek_result(requirement, done, admission_year))
        elif requirement.id == ABEEK_DESIGN_ID:
            results.append(design_result(requirement, done))
        elif requirement.id in LIBERAL_AREAS:
            results.append(liberal_result(requirement, done))
        elif requirement.id == SDG_ID:
            results.append(sdg_result(requirement, done))
        elif requirement.id == STARTUP_CREDITS_ID:
            results.append(startup_result(requirement, done))
    return results


def auto_satisfied(requirement: NonacademicRequirement, courses: list[Course],
                   equivalents: dict[str, str]) -> str | None:
    """체크리스트 항목 중 이수 과목만으로 충족이 확인되면 그 근거를 돌려준다."""
    done = completed_courses(courses, equivalents)

    def area_courses(name: str) -> list[Course]:
        return [course for course in done
                if is_liberal(course) and (area := liberal_area_of(course)) and area.area == name]

    if requirement.id == SOFTWARE_ID:
        # 붙임3: 첨성인기초 소프트웨어 3학점 또는 컴퓨터학부 지정 대체과목 3학점
        substitute = [course for course in done if course.course_code in requirement.course_codes]
        if sum(course.credits for course in substitute) >= 3:
            return "컴퓨터학부 지정 대체과목 이수: " + ", ".join(map(describe, substitute))
        sw = area_courses(SOFTWARE_AREA)
        if sum(course.credits for course in sw) >= 3:
            return "첨성인기초(소프트웨어) 3학점 이상 이수: " + ", ".join(map(describe, sw))
    elif requirement.id == PRACTICAL_ENGLISH_ID and requirement.minimum == 1:
        # 붙임3: 실용영어영역 중 4학점을 이수한 자 (TOPIK 대체 요건은 제외)
        english = area_courses(PRACTICAL_ENGLISH_AREA)
        if sum(course.credits for course in english) >= 4:
            return "첨성인기초(실용영어) 4학점 이상 이수: " + ", ".join(map(describe, english))
    elif requirement.id == STARTUP_ACTIVITY_ID:
        counted = startup_credit_courses(done)
        if sum(course.credits for course in counted) >= STARTUP_ACTIVITY_CREDITS:
            return f"창업교과목 총 {STARTUP_ACTIVITY_CREDITS}학점 이상 이수로 대체: " + ", ".join(map(describe, counted))
    return None

