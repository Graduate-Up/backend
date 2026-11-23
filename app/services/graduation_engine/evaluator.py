from collections import Counter

from app.schemas.course import Course
from app.schemas.evaluation import (
    CreditEntry, EvaluationResponse, NonacademicRecord, RequirementCounts, RequirementResult,
)
from app.schemas.rules import GraduationRule, NonacademicRequirement
from app.schemas.student import StudentProfile

from .course_areas import COMPUTED_IDS, area_results, auto_satisfied
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


def equivalent_codes(rule: GraduationRule) -> dict[str, str]:
    return {alias: code for code, aliases in rule.equivalent_courses.items() for alias in aliases}


def evaluate_course_requirements(
    completed_courses: list[Course], rule: GraduationRule, admission_year: int,
) -> tuple[list[RequirementResult], list[CreditEntry]]:
    """Requirements decided only by the student's course list: credits, required courses, GPA and areas."""
    equivalents = equivalent_codes(rule)
    requirements, entries = check_credits(completed_courses, rule, equivalents)
    requirements.extend(check_required_courses(completed_courses, item, equivalents)
                        for item in rule.course_requirements)
    requirements.extend(gpa_result(item, completed_courses, equivalents)
                        for item in rule.nonacademic_requirements if item.id == GPA_REQUIREMENT_ID)
    requirements.extend(area_results(rule, completed_courses, equivalents, admission_year))
    return requirements, entries


def evaluate_graduation(
    student_profile: StudentProfile,
    completed_courses: list[Course],
    nonacademic_records: list[NonacademicRecord],
    rules: RuleSource,
) -> EvaluationResponse:
    rule = select_rules(student_profile, rules)
    requirements, entries = evaluate_course_requirements(completed_courses, rule, student_profile.admission_year)
    equivalents = equivalent_codes(rule)

    for item in checklist_requirements(rule):
        evidence = auto_satisfied(item, completed_courses, equivalents)
        if evidence:
            requirements.append(RequirementResult(
                id=item.id, name=item.name, status="satisfied", current=item.minimum, required=item.minimum,
                shortfall=0, reason=evidence, sources=item.sources, details=[evidence],
            ))
            continue
        records = [record for record in nonacademic_records if record.requirement_id == item.id]
        # Each record is a snapshot of the requirement, so repeated submissions are not summed.
        current = max((record.amount for record in records if record.status == "satisfied"), default=0)
        shortfall = max(0, item.minimum - current)
        if not shortfall:
            status = "satisfied"
        elif any(record.status == "verification_required" for record in records):
            status = "verification_required"
        elif any(record.status == "approval_required" for record in records):
            status = "approval_required"
        else:
            status = "unsatisfied"
        requirements.append(RequirementResult(
            id=item.id, name=item.name, status=status, current=current, required=item.minimum,
            shortfall=shortfall, reason="Compared with submitted nonacademic records", sources=item.sources,
        ))

    requirements.extend(RequirementResult(
        id=item.id, name=item.name, status="verification_required",
        current=0, required=1, shortfall=1,
        reason="Additional evidence or interpretation is needed to automate this requirement",
        sources=item.sources,
    ) for item in rule.verification_requirements)

    states = Counter(item.status for item in requirements)
    status = next((state for state in ("verification_required", "approval_required", "unsatisfied")
                   if state in states), "satisfied")
    return EvaluationResponse(
        rule_id=rule.id, dataset=rule.dataset, eligible=status == "satisfied", status=status,
        requirements=requirements, credit_entries=entries, progress=completion_percent(requirements),
        requirement_counts=RequirementCounts(**states),
    )
