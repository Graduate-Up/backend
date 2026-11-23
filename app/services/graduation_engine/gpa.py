"""평점평균(GPA) 계산.

경북대 4.3 만점, F 포함. 재이수는 needs_confirmation NC-RETAKE-GRADE 의 기본 적용값대로
같은 과목(동일과목 포함)의 마지막 성적만 반영한다. P/NP 과목은 평점에서 제외한다.
"""

from app.schemas.course import GRADE_POINTS, Course
from app.schemas.evaluation import RequirementResult
from app.schemas.rules import NonacademicRequirement

GPA_REQUIREMENT_ID = "gpa"
TERM_ORDER = {"1": 1, "summer": 2, "2": 3, "winter": 4}


def semester_key(semester: str) -> tuple[str, int]:
    year, _, term = semester.partition("-")
    return year, TERM_ORDER.get(term, 0)


def compute_gpa(courses: list[Course], equivalents: dict[str, str]) -> tuple[float | None, float, int]:
    """Return (gpa, graded credits, number of finished courses without a grade)."""
    latest: dict[str, Course] = {}
    for course in courses:
        if course.status not in ("completed", "failed") or course.grade not in GRADE_POINTS or course.credits <= 0:
            continue
        code = equivalents.get(course.course_code, course.course_code)
        if code not in latest or semester_key(course.semester) >= semester_key(latest[code].semester):
            latest[code] = course
    ungraded = sum(course.status == "completed" and course.grade is None for course in courses)
    credits = sum(course.credits for course in latest.values())
    if not credits:
        return None, 0, ungraded
    points = sum(GRADE_POINTS[course.grade] * course.credits for course in latest.values())
    # 학교 성적표처럼 소수 둘째 자리에서 끊어 비교한다.
    # round 먼저: 3.3 * 3 / 3 == 3.2999… 같은 부동소수점 오차로 한 단계 내려가지 않게 한다.
    return int(round(points / credits * 100, 6)) / 100, credits, ungraded


def gpa_result(
    requirement: NonacademicRequirement, courses: list[Course], equivalents: dict[str, str],
) -> RequirementResult:
    gpa, credits, ungraded = compute_gpa(courses, equivalents)
    base = dict(id=requirement.id, name=requirement.name, required=requirement.minimum, sources=requirement.sources)
    if gpa is None:
        reason = "성적등급이 있는 과목이 없어 평점을 계산할 수 없어요. 이수 내역을 다시 가져오거나 과목에 성적을 입력해 주세요."
        return RequirementResult(
            **base, status="verification_required", current=0, shortfall=requirement.minimum,
            reason=reason, details=[*requirement.details, reason],
        )
    reason = f"성적등급이 있는 {credits:g}학점 기준 (재수강은 마지막 성적)"
    if ungraded:
        reason += f" · 성적 미입력 {ungraded}과목 제외"
    shortfall = max(0, round(requirement.minimum - gpa, 2))
    return RequirementResult(
        **base, status="unsatisfied" if shortfall else "satisfied", current=gpa, shortfall=shortfall, reason=reason,
        details=[*requirement.details, reason],
    )
