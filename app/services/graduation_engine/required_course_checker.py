from app.schemas.course import Course
from app.schemas.evaluation import RequiredCourseResult, RequirementResult
from app.schemas.rules import CourseRequirement


def check_required_courses(
    courses: list[Course], requirement: CourseRequirement, equivalents: dict[str, str],
) -> RequirementResult:
    if not requirement.accept_equivalent_codes:
        equivalents = {}
    completed = set()
    verification = set()
    approval = set()
    for course in courses:
        if course.status != "completed":
            continue
        code = equivalents.get(course.course_code, course.course_code)
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
        if requirement.require_department and course.offered_by_computer_science is None:
            verification.add(code)
        elif requirement.required_transcript_category and (
            course.transcript_category.strip() != requirement.required_transcript_category
        ):
            if course.transcript_category.strip() != "다중전공":
                approval.add(code)
        else:
            completed.add(code)
    in_progress = {equivalents.get(c.course_code, c.course_code) for c in courses if c.status == "in_progress"}
    accepted = [
        {equivalents.get(code, code) for code in (option.course_code, *option.substitutes)}
        for option in requirement.courses
    ]
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
        reason="Matched course codes and applicable offering and transcript classification rules",
        sources=requirement.sources, required_courses=details,
    )
