from collections import Counter

from app.schemas.course import Course
from app.schemas.evaluation import (
    CreditEntry, EarlyGraduationAssessment, EvaluationResponse, LiberalSummary, NonacademicRecord,
    ProgressNotice, RequirementCounts, RequirementResult,
)
from app.schemas.rules import GraduationRule, NonacademicRequirement
from app.schemas.student import StudentProfile

from .course_areas import (
    COMPUTED_IDS, FIELD_PRACTICE_ID, area_results, auto_satisfied, course_labels, describe, field_practice_courses,
)
from .credit_checker import check_credits
from .gpa import GPA_REQUIREMENT_ID, gpa_result
from .required_course_checker import check_required_courses
from .rule_selector import RuleSource, select_rules


def requirement_fraction(item: RequirementResult) -> float:
    """Progress share of one requirement (0~1).

    Credits and required courses count partially (current / required). GPA is pass/fail:
    graduation only asks whether it reaches the minimum, so it is 1 or 0.
    """
    if item.status == "satisfied":
        return 1.0
    if item.id == GPA_REQUIREMENT_ID or not item.required:
        return 0.0
    return min(1.0, item.current / item.required)


def completion_percent(requirements: list[RequirementResult]) -> float:
    if not requirements:
        return 0
    if all(item.status == "satisfied" for item in requirements):
        return 100
    fractions = [requirement_fraction(item) for item in requirements]
    return min(99, round(100 * sum(fractions) / len(fractions), 1))


def checklist_requirements(rule: GraduationRule) -> list[NonacademicRequirement]:
    """Nonacademic requirements the student confirms manually.

    GPA (from grades) and area requirements such as ABEEK areas, liberal-arts areas and SDG
    (from the course → area tables) are calculated from the course list instead.
    """
    return [item for item in rule.nonacademic_requirements
            if item.id != GPA_REQUIREMENT_ID and item.id not in COMPUTED_IDS]


def recorded_amount(requirement: NonacademicRequirement, records: list[NonacademicRecord]) -> float | None:
    return max((record.amount for record in records
                if record.requirement_id == requirement.id and record.status == "satisfied"), default=None)


def equivalent_codes(rule: GraduationRule) -> dict[str, str]:
    return {alias: code for code, aliases in rule.equivalent_courses.items() for alias in aliases}


def rule_notices(rule: GraduationRule) -> list[ProgressNotice]:
    return [ProgressNotice(id=item.id, name=item.name, sources=item.sources)
            for item in rule.verification_requirements]


def early_graduation_assessment(
    rule: GraduationRule, profile: StudentProfile, requirements: list[RequirementResult],
) -> EarlyGraduationAssessment | None:
    policy = rule.early_graduation
    if policy is None:
        return None
    gpa = next((item.current for item in requirements
                if item.id == GPA_REQUIREMENT_ID and item.status != "verification_required"), None)
    semesters = profile.registered_semesters
    requirements_met = all(item.status == "satisfied" for item in requirements)
    if policy.not_for_transfer and (profile.transfer_student or profile.student_status in
                                   {"general_transfer", "bachelor_transfer"}):
        status = "not_applicable"
    elif (semesters is not None and semesters < policy.min_semesters) or (gpa is not None and gpa < policy.min_gpa):
        status = "criteria_not_met"
    elif semesters is None or gpa is None:
        status = "verification_required"
    else:
        status = "criteria_met" if requirements_met else "criteria_not_met"
    return EarlyGraduationAssessment(
        status=status, registered_semesters=semesters, gpa=gpa,
        graduation_requirements_met=requirements_met, policy=policy,
    )


def evaluate_checklist(
    item: NonacademicRequirement, courses: list[Course], equivalents: dict[str, str],
    records: list[NonacademicRecord],
) -> tuple[RequirementResult, float | None, bool]:
    matches = [record for record in records if record.requirement_id == item.id]
    # Manual entries and transcript credits are independent confirmations of the same requirement.
    recorded = recorded_amount(item, matches)
    evidence = None
    automatic = 0.0
    if item.id == FIELD_PRACTICE_ID:
        counted = field_practice_courses(courses, equivalents)
        automatic = sum(course.credits for course in counted)
        if counted:
            evidence = "이수 과목에서 인정한 현장실습: " + ", ".join(map(describe, counted))
    else:
        evidence = auto_satisfied(item, courses, equivalents)
        if evidence:
            automatic = item.minimum
    current = max(automatic, recorded or 0)
    shortfall = max(0, item.minimum - current)
    status = "satisfied" if not shortfall else next(
        (state for state in ("verification_required", "approval_required")
         if any(record.status == state for record in matches)), "unsatisfied",
    )
    return RequirementResult(
        id=item.id, name=item.name, status=status, current=current, required=item.minimum,
        shortfall=shortfall, reason=evidence or "학생이 입력한 비교과 기록으로 확인",
        sources=item.sources, details=([evidence] if evidence else []) + item.details,
    ), recorded, evidence is not None and automatic >= item.minimum


def evaluate_course_requirements(
    completed_courses: list[Course], rule: GraduationRule, admission_year: int,
    liberal_summary: LiberalSummary | None = None,
) -> tuple[list[RequirementResult], list[CreditEntry]]:
    """Requirements decided only by the student's course list: credits, required courses, GPA and areas."""
    equivalents = equivalent_codes(rule)
    requirements, entries = check_credits(completed_courses, rule, equivalents)
    for entry, course in zip(entries, completed_courses, strict=True):
        entry.course_areas = course_labels(course, rule.major, admission_year)
    requirements.extend(check_required_courses(completed_courses, item, equivalents)
                        for item in rule.course_requirements)
    requirements.extend(gpa_result(item, completed_courses, equivalents)
                        for item in rule.nonacademic_requirements if item.id == GPA_REQUIREMENT_ID)
    requirements.extend(area_results(rule, completed_courses, equivalents, admission_year, liberal_summary))
    return requirements, entries


def evaluate_graduation(
    student_profile: StudentProfile,
    completed_courses: list[Course],
    nonacademic_records: list[NonacademicRecord],
    rules: RuleSource,
    liberal_summary: LiberalSummary | None = None,
) -> EvaluationResponse:
    rule = select_rules(student_profile, rules)
    return evaluate_selected_rule(student_profile, completed_courses, nonacademic_records, rule, liberal_summary)


def evaluate_selected_rule(
    student_profile: StudentProfile,
    completed_courses: list[Course],
    nonacademic_records: list[NonacademicRecord],
    rule: GraduationRule,
    liberal_summary: LiberalSummary | None = None,
) -> EvaluationResponse:
    requirements, entries = evaluate_course_requirements(
        completed_courses, rule, student_profile.admission_year, liberal_summary,
    )
    equivalents = equivalent_codes(rule)

    requirements.extend(evaluate_checklist(item, completed_courses, equivalents, nonacademic_records)[0]
                        for item in checklist_requirements(rule))

    states = Counter(item.status for item in requirements)
    status = next((state for state in ("verification_required", "approval_required", "unsatisfied")
                   if state in states), "satisfied")
    return EvaluationResponse(
        rule_id=rule.id, dataset=rule.dataset, eligible=status == "satisfied", status=status,
        requirements=requirements, credit_entries=entries, progress=completion_percent(requirements),
        requirement_counts=RequirementCounts(**states),
        notices=rule_notices(rule),
        early_graduation=early_graduation_assessment(rule, student_profile, requirements),
    )
