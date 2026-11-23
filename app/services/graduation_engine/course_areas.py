"""이수 과목이 어느 영역에 속하는지로 판정하는 요건.

학생이 체크하던 ABEEK 영역(기본소양·전공기반·공학전공·설계), 교양 영역(첨성인기초·첨성인핵심),
SDG 교양, 창업교과목(글솝 기술창업역량)을 과목코드 매핑표로 계산하고, SW·실용영어 졸업자격인정과
스타트업 창업은 과목으로 충족이 확인되면 자동 체크한다.

- 전공·학번별 교육과정표: data/curricula/curricula.csv (scripts/build_curricula.py로 생성)
- 교양 영역(학기별): data/curricula/raw/cltr_areas.json (knuin 수업시간표·강의계획서 조회, 2019~2026)
- 교양 영역(2025 공식 목록)·SDG·창업교과목: data/course_classification/
"""

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.schemas.course import Course
from app.schemas.evaluation import LiberalSummary, RequirementResult
from app.schemas.rules import GraduationRule, NonacademicRequirement

from .gpa import semester_key

ROOT = Path(__file__).resolve().parents[3]
DATA_DIRECTORY = ROOT / "data" / "course_classification"
CURRICULA_FILE = ROOT / "data" / "curricula" / "curricula.csv"
LIBERAL_TERMS_FILE = ROOT / "data" / "curricula" / "raw" / "cltr_areas.json"

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

ABEEK_CATEGORIES = {"기본소양": "basic", "전공기반": "msc", "공학전공": "major", "교직": "teaching"}
LIBERAL_GROUPS = {"foundation_a": "첨성인기초", "foundation_b": "첨성인기초",
                  "core_humanities": "첨성인핵심", "core_science": "첨성인핵심"}
LIBERAL_REFORM_YEAR = 2023  # 2023학년도 교양 영역 개편
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


@dataclass(frozen=True)
class CurriculumCourse:
    code: str
    name: str
    credits: float
    category: str
    required: bool
    design: bool
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Curriculum:
    """한 학번에 적용되는 교육과정표."""
    admission_year: int
    curriculum_year: int  # 적용 교육과정 학년도 (예: 20학번 → 2021 '2020~2021' 표)
    inferred: bool  # 그 학번 원문이 없어 다른 학년도 표를 적용한 경우
    courses: dict[str, CurriculumCourse]


@lru_cache(maxsize=1)
def curricula() -> dict[str, dict[int, Curriculum]]:
    """major → 학번(입학년도) → 교육과정표."""
    tables: dict[str, dict[int, Curriculum]] = {}
    with open(CURRICULA_FILE, encoding="utf-8") as file:
        for row in csv.DictReader(file):
            admission = int(row["admission_year"])
            table = tables.setdefault(row["major"], {}).setdefault(admission, Curriculum(
                admission, int(row["curriculum_year"]), row["inferred"] == "true", {}))
            table.courses[row["code"]] = CurriculumCourse(
                row["code"], row["name"], float(row["credits"]), row["category"], row["required"] == "true",
                row["design"] == "true", tuple(tag for tag in row["tags"].split(",") if tag))
    return tables


def curriculum_table(major: str, admission_year: int) -> Curriculum:
    """입학년도(학번) 기준 교육과정표. 표가 없는 학번은 그 이전 가장 가까운 학번(없으면 가장 이른 학번) 표로 추정."""
    tables = curricula()[major]
    years = sorted(tables)
    return tables[max([year for year in years if year <= admission_year] or [years[0]])]


def curriculum_year(major: str, admission_year: int) -> int:
    return curriculum_table(major, admission_year).curriculum_year


def curriculum(major: str, admission_year: int) -> dict[str, CurriculumCourse]:
    return curriculum_table(major, admission_year).courses


def curriculum_is_sourced(major: str, admission_year: int) -> bool:
    """그 학번의 교육과정 원문이 있으면 True (없으면 다른 학번·학년도 표로 추정한 것)."""
    table = curricula().get(major, {}).get(admission_year)
    return table is not None and not table.inferred


@lru_cache(maxsize=1)
def liberal_terms() -> dict[str, list[tuple[LiberalArea, frozenset[str]]]]:
    """과목코드 → [(그 학기의 교양 영역, 개설 학기들)]. 같은 과목도 학기에 따라 영역이 다를 수 있다."""
    data = json.loads(LIBERAL_TERMS_FILE.read_text(encoding="utf-8"))
    terms: dict[str, list[tuple[LiberalArea, frozenset[str]]]] = {}
    for item in data["courses"]:
        # 경로: [첨성인일반] · [첨성인소양, 예술과표현] · [첨성인핵심, 인문사회, 인간과사회]
        path = item["area"]
        group = path[0]
        area = AREA_ALIASES.get(path[1], path[1]) if len(path) > 1 else group
        terms.setdefault(item["code"], []).append(
            (LiberalArea(item["name"], group, area, False), frozenset(item["offered"])))
    return terms


@lru_cache(maxsize=1)
def startup_courses() -> dict[str, str]:
    with open(DATA_DIRECTORY / "startup_courses.csv", encoding="utf-8") as file:
        return {row["code"]: row["name"] for row in csv.DictReader(file)}


def abeek_table_year(admission_year: int) -> int:
    return curriculum_year("plsop", admission_year)


def known_course_name(code: str) -> str | None:
    """학부 교육과정표에 있는 과목명 (최신 학년도 우선)."""
    for tables in curricula().values():
        for year in sorted(tables, reverse=True):
            if code in tables[year].courses:
                return tables[year].courses[code].name
    return None


def major_courses(major: str, admission_year: int, categories: set[str]) -> dict[str, str]:
    return {code: course.name for code, course in curriculum(major, admission_year).items()
            if course.category in categories}


# 전공학점으로 세는 교육과정 구분
MAJOR_CATEGORIES = {"plsop": {"공학전공"}, "glsop": {"전공"}, "aicom": {"전공"}, "simcom": {"전공"},
                    "chumcom": {"전공"}}


def glsop_major_courses(admission_year: int) -> dict[str, str]:
    """글솝 전공으로 인정되는 과목: 본인 입학년도 기준 교육과정의 전공 교과목 (글솝 공지 28594)."""
    return major_courses("glsop", admission_year, {"전공"})


def plsop_major_courses(admission_year: int) -> dict[str, str]:
    """플솝 전공학점 과목: 본인 입학년도 기준 교육과정의 공학전공 교과목."""
    return major_courses("plsop", admission_year, {"공학전공"})


def same_name(name: str) -> str:
    return "".join(name.split()).replace("Ⅰ", "I").replace("Ⅱ", "II")


def abeek_area(code: str, admission_year: int) -> tuple[str | None, str | None]:
    """Return (area, renamed-from code) from the student's admission-year table.

    A code missing from that table counts only when it is the same course under a new code
    (e.g. 미래산업과 직업선택 CLTR0689 → FUTR0208): another year's table gives the course name and the
    student's own table has a course with that name. Courses dropped from the student's table do not count.
    """
    own = curriculum("plsop", admission_year)
    if code in own:
        return ABEEK_CATEGORIES[own[code].category], None
    name = next((table.courses[code].name for year, table in sorted(curricula()["plsop"].items(), reverse=True)
                 if code in table.courses), None)
    if name is not None:
        for match in own.values():
            if same_name(match.name) == same_name(name):
                return ABEEK_CATEGORIES[match.category], match.code
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
        found, renamed = abeek_area(course.course_code, admission_year)
        if found == area:
            counted.append(course)
            if renamed:
                borrowed.append(f"{course.course_code}(= {renamed})")
    current = sum(course.credits for course in counted)
    reason = f"플솝 {own}학년도 기준 교육과정의 ABEEK 영역 표로 계산"
    uncertain = []
    if not curriculum_is_sourced("plsop", admission_year):
        uncertain.append(f"{admission_year}학번 교육과정표가 학부 사이트에 없어 {own}학년도 표로 추정했어요")
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 과목 없음"]
    if borrowed:
        details.append("과목코드만 바뀐 같은 과목으로 인정: " + ", ".join(borrowed))
    return result(requirement, current, uncertain=uncertain, reason=reason, details=details)


def design_result(requirement: NonacademicRequirement, courses: list[Course]) -> RequirementResult:
    counted = [(course, requirement.course_credits[course.course_code]) for course in courses
               if course.course_code in requirement.course_credits]
    current = sum(amount for _, amount in counted)
    details = ["인정: " + ", ".join(f"{course.course_code} 설계 {amount:g}학점" for course, amount in counted)
               if counted else "인정된 설계 과목 없음"]
    return result(requirement, current, uncertain=[], reason="ABEEK 설계 과목별 설계학점 합계", details=details)


def term_year(semester: str) -> int | None:
    year = semester.partition("-")[0]
    return int(year) if year.isdigit() else None


def liberal_area_of(course: Course) -> LiberalArea | None:
    """수강 학기 기준 교양 영역. 그 학기 분류 → 같은 시기(2023 개편 전/후)의 가장 가까운 학기 분류 →
    2025 공식 목록 → 성적표 교과구분 순으로 찾는다."""
    official = liberal_areas().get(course.course_code)
    sdg = bool(official and official.sdg)
    year = term_year(course.semester)
    entries = liberal_terms().get(course.course_code, [])
    if entries and year is not None:
        label = f"{course.semester}학기"
        exact = [area for area, terms in entries if label in terms]
        if exact:
            return LiberalArea(exact[0].name, exact[0].group, exact[0].area, sdg)
        after_reform = year >= LIBERAL_REFORM_YEAR
        same_era = [(min(abs(int(term[:4]) - year) for term in terms), area) for area, terms in entries
                    if any((int(term[:4]) >= LIBERAL_REFORM_YEAR) == after_reform for term in terms)]
        if same_era:
            area = min(same_era, key=lambda item: item[0])[1]
            return LiberalArea(area.name, area.group, area.area, sdg)
    if official is not None:
        return official
    area = transcript_area(course)
    return LiberalArea(course.course_name or "", "", area, False) if area else None


def liberal_result(
    requirement: NonacademicRequirement, courses: list[Course], liberal_summary: LiberalSummary | None = None,
) -> RequirementResult:
    targets = LIBERAL_AREAS[requirement.id]
    if liberal_summary is not None:
        areas = [(area, credits) for area, credits in liberal_summary.items() if area in targets]
        return result(
            requirement, sum(credits for _, credits in areas), uncertain=[],
            reason="통합정보시스템 첨성인 교양과목 이수현황의 영역별 학점으로 계산",
            details=[f"{area} {credits:g}학점" for area, credits in areas],
        )
    counted, unknown = [], []
    for course in courses:
        if course.prior_institution or not is_liberal(course):
            continue  # 영역별 필수과목은 경북대 개설 과목만 인정
        area = liberal_area_of(course)
        if area is None:
            unknown.append(course)
        elif area.area in targets and area.group in ("", LIBERAL_GROUPS[requirement.id]):
            counted.append(course)
    current = sum(course.credits for course in counted)
    uncertain = ["영역을 확인하지 못한 교양: " + ", ".join(map(describe, unknown))] if unknown else []
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 과목 없음"]
    reason = "수강 학기의 교양 영역(수업시간표 조회 2019~2026), 2025 이수영역별 교양과목 목록, 성적표 교과구분으로 계산"
    return result(requirement, current, uncertain=uncertain, reason=reason, details=details)


def sdg_result(requirement: NonacademicRequirement, courses: list[Course]) -> RequirementResult:
    counted = [course for course in courses
               if not course.prior_institution and (area := liberal_areas().get(course.course_code)) and area.sdg]
    current = sum(course.credits for course in counted)
    details = ["인정: " + ", ".join(map(describe, counted)) if counted else "인정된 SDG 과목 없음"]
    return result(requirement, current, uncertain=[], details=details,
                  reason="이수영역별 교양과목 목록(2025)의 SDG 표시 과목으로 계산")


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


ABEEK_NAMES = {area: name for name, area in ABEEK_CATEGORIES.items()}


def course_labels(course: Course, major: str, admission_year: int) -> list[str]:
    """과목 하나가 어느 구분에 해당하는지 (화면 표시용).

    - 입학년도 교육과정표의 이수구분: 플솝은 기본소양·전공기반·공학전공·교직, 그 외는 교양·전공·일반선택
      (+ 필수, 설계)
    - 교양은 수강 학기 기준 영역: 첨성인기초·첨성인핵심·첨성인일반·첨성인소양 (+ 세부 영역)
    - SDG 교양, 글솝은 창업교과목 여부
    """
    labels = []
    code = course.course_code
    if major in curricula():
        own = curriculum(major, admission_year)
        entry = own.get(code)
        if entry is None and major == "plsop":
            area, renamed = abeek_area(code, admission_year)
            entry = own.get(renamed) if area else None
        if entry is not None:
            labels.append(entry.category)
            labels.extend(flag for flag, on in (("필수", entry.required), ("설계", entry.design)) if on)
    if is_liberal(course) and (area := liberal_area_of(course)):
        if area.group and area.area and area.area != area.group:
            labels.append(f"{area.group}({area.area})")
        else:
            labels.append(area.group or area.area)
        if area.sdg:
            labels.append("SDG")
    if major == "glsop" and not course.prior_institution and is_startup_course(course):
        labels.append("창업교과목")
    return labels


def area_results(rule: GraduationRule, courses: list[Course], equivalents: dict[str, str],
                 admission_year: int, liberal_summary: LiberalSummary | None = None) -> list[RequirementResult]:
    done = completed_courses(courses, equivalents)
    results = []
    for requirement in rule.nonacademic_requirements:
        if requirement.id in ABEEK_AREAS:
            results.append(abeek_result(requirement, done, admission_year))
        elif requirement.id == ABEEK_DESIGN_ID:
            results.append(design_result(requirement, done))
        elif requirement.id in LIBERAL_AREAS:
            results.append(liberal_result(requirement, done, liberal_summary))
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
        return [course for course in done if is_liberal(course) and (area := liberal_area_of(course))
                and area.area == name and area.group in ("", "첨성인기초")]

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

