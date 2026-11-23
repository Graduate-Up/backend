from unicodedata import normalize

from app.schemas.course import MAJOR_TRANSCRIPT_CATEGORIES, Course
from app.schemas.evaluation import RequiredCourseResult, RequirementResult
from app.schemas.rules import CourseRequirement

from .credit_checker import NON_MAJOR_CATEGORIES


def _name_key(name: str) -> str:
    return "".join(normalize("NFKC", name).split()).casefold()


def check_required_courses(
    courses: list[Course], requirement: CourseRequirement, equivalents: dict[str, str],
) -> RequirementResult:
    if not requirement.accept_equivalent_codes:
        equivalents = {}
    accepted = [
        {equivalents.get(code, code) for code in (option.course_code, *option.substitutes)}
        for option in requirement.courses
    ]
    known_codes = set().union(*accepted)
    codes_by_name = {
        _name_key(option.course_name): equivalents.get(option.course_code, option.course_code)
        for option in requirement.courses if requirement.match_by_course_name
    }

    def matched_code(course: Course) -> str:
        code = equivalents.get(course.course_code, course.course_code)
        if code in known_codes or not course.course_name:
            return code
        return codes_by_name.get(_name_key(course.course_name), code)

    completed = set()
    verification = set()
    approval = set()
    for course in courses:
        if course.status != "completed":
            continue
        code = matched_code(course)
        if code not in known_codes:
            continue
        category = course.transcript_category.strip()
        if course.credits < requirement.minimum_course_credits:
            continue
        if requirement.reject_transfer_courses:
            if course.prior_institution is True:
                continue
            if course.prior_institution is None and course.offered_by_computer_science is not True:
                verification.add(code)
                continue
        if requirement.require_department and course.offered_by_computer_science is False:
            continue
        if requirement.require_department and not requirement.match_by_course_name and category in NON_MAJOR_CATEGORIES:
            continue
        required_category = requirement.required_transcript_category
        if required_category and category != required_category and (required_category, category) != ("전공필수", "전공"):
            if category not in NON_MAJOR_CATEGORIES:
                approval.add(code)
        elif requirement.require_department and course.offered_by_computer_science is None and (
            category not in MAJOR_TRANSCRIPT_CATEGORIES and not requirement.match_by_course_name
        ):
            verification.add(code)
        else:
            completed.add(code)
    in_progress = {matched_code(course) for course in courses if course.status == "in_progress"}
    assignments: dict[str, int] = {}

    # Reassign a shared substitute when another completed course can fill its current slot.
    def assign(index: int, visited: set[str]) -> bool:
        for code in sorted(accepted[index] & completed):
            if code in visited:
                continue
            visited.add(code)
            if code not in assignments or assign(assignments[code], visited):
                assignments[code] = index
                return True
        return False

    for index in range(len(accepted)):
        assign(index, set())

    matched_options = set(assignments.values())
    details = []
    for index, option in enumerate(requirement.courses):
        if index in matched_options:
            status = "completed"
        elif accepted[index] & (verification - completed):
            status = "verification_required"
        elif accepted[index] & (approval - completed):
            status = "approval_required"
        elif accepted[index] & in_progress:
            status = "in_progress"
        else:
            status = "missing"
        details.append(RequiredCourseResult(
            course_code=option.course_code, course_name=option.course_name, status=status,
        ))

    count = len(matched_options)
    shortfall = max(0, requirement.choose - count)
    status = "satisfied"
    if shortfall:
        detail_statuses = {detail.status for detail in details}
        status = next((state for state in ("verification_required", "approval_required")
                       if state in detail_statuses), "unsatisfied")
    return RequirementResult(
        id=requirement.id, name=requirement.name, status=status,
        current=count, required=requirement.choose, shortfall=shortfall,
        reason="Matched course codes or configured names and applied transcript classification rules",
        sources=requirement.sources, required_courses=details,
    )
