"""Dashboard progress.

Only course-based requirements (credits, required courses, GPA from grades) are calculated here. Nonacademic
requirements such as TOEIC, counseling or field practice become checklist items: the frontend
toggles them and recomputes the overall percentage with the same formula as ``overall_percent``.
"""

from app.schemas.course import Course
from app.schemas.evaluation import (
    NonacademicRecord, ProgressItem, ProgressNotice, ProgressResponse, RequirementResult,
)
from app.schemas.rules import GraduationRule, NonacademicRequirement
from app.schemas.student import StudentProfile

from .course_areas import COMPUTED_IDS, auto_satisfied
from .evaluator import checklist_requirements, equivalent_codes, evaluate_course_requirements, requirement_fraction
from .gpa import GPA_REQUIREMENT_ID
from .rule_selector import RuleSource, select_rules


def overall_percent(items: list[ProgressItem]) -> float:
    """Equal weight per item. 100% only when every item is complete; otherwise at most 99%.

    Keep in sync with ``computeProgress`` in ``app/dashboard-view.js``.
    """
    if not items:
        return 0
    if all(is_complete(item) for item in items):
        return 100
    return min(99, round(100 * sum(item.fraction for item in items) / len(items), 1))


def is_complete(item: ProgressItem) -> bool:
    return bool(item.checked) if item.kind == "checklist" else item.status == "satisfied"


def is_checked(requirement: NonacademicRequirement, records: list[NonacademicRecord]) -> bool:
    return any(record.requirement_id == requirement.id and record.status == "satisfied"
               and record.amount >= requirement.minimum for record in records)


def course_item(result: RequirementResult) -> ProgressItem:
    fraction = requirement_fraction(result)
    return ProgressItem(
        id=result.id, name=result.name, kind="course", status=result.status,
        current=result.current, required=result.required,
        unit=unit_for(result), fraction=fraction, reason=result.reason, details=result.details,
        credit_area=result.credit_area, required_courses=result.required_courses, sources=result.sources,
    )


def unit_for(result: RequirementResult) -> str:
    if result.credit_area or result.id in COMPUTED_IDS:
        return "학점"
    return "점" if result.id == GPA_REQUIREMENT_ID else "과목"


def checklist_item(
    requirement: NonacademicRequirement, records: list[NonacademicRecord], evidence: str | None,
) -> ProgressItem:
    checked = evidence is not None or is_checked(requirement, records)
    return ProgressItem(
        id=requirement.id, name=requirement.name, kind="checklist",
        status="satisfied" if checked else "unsatisfied",
        current=requirement.minimum if checked else 0, required=requirement.minimum, unit=requirement.unit,
        fraction=1.0 if checked else 0.0, checked=checked, auto_checked=evidence is not None,
        reason=evidence or "학생이 직접 충족 여부를 체크하는 항목",
        details=[evidence] if evidence else requirement.details, sources=requirement.sources,
    )


def build_progress(
    student_profile: StudentProfile,
    completed_courses: list[Course],
    nonacademic_records: list[NonacademicRecord],
    rules: RuleSource,
) -> ProgressResponse:
    rule = select_rules(student_profile, rules)
    results, entries = evaluate_course_requirements(completed_courses, rule, student_profile.admission_year)
    equivalents = equivalent_codes(rule)
    course_items = [course_item(result) for result in results]
    items = course_items + [
        checklist_item(item, nonacademic_records, auto_satisfied(item, completed_courses, equivalents))
        for item in checklist_requirements(rule)
    ]
    return ProgressResponse(
        rule_id=rule.id, progress=overall_percent(items), course_progress=overall_percent(course_items),
        items=items, credit_entries=entries,
        notices=[ProgressNotice(id=item.id, name=item.name, sources=item.sources)
                 for item in rule.verification_requirements],
    )


def toggle_checklist(
    rule: GraduationRule, records: list[NonacademicRecord], requirement_id: str, checked: bool,
) -> list[NonacademicRecord]:
    """Return the nonacademic records with one checklist item set or cleared."""
    requirement = next((item for item in checklist_requirements(rule) if item.id == requirement_id), None)
    if requirement is None:
        raise KeyError(requirement_id)
    kept = [record for record in records if record.requirement_id != requirement_id]
    if checked:
        kept.append(NonacademicRecord(requirement_id=requirement_id, amount=requirement.minimum, status="satisfied"))
    return kept
