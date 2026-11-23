"""Dashboard progress.

Only course-based requirements (credits, required courses, GPA from grades) are calculated here. Nonacademic
requirements such as TOEIC, counseling or field practice become checklist items: the frontend
toggles them and recomputes the overall percentage with the same formula as ``overall_percent``.
"""

from app.schemas.course import Course
from app.schemas.evaluation import (
    EnglishTestResult, LiberalSummary, NonacademicRecord, ProgressItem, ProgressResponse, RequirementResult,
)
from app.schemas.rules import GraduationRule, NonacademicRequirement
from app.schemas.student import StudentProfile

from .course_areas import COMPUTED_IDS
from .evaluator import (
    checklist_requirements, early_graduation_assessment, equivalent_codes, evaluate_checklist,
    evaluate_course_requirements, recorded_amount, requirement_fraction, rule_notices,
)
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
    amount = recorded_amount(requirement, records)
    return amount is not None and amount >= requirement.minimum


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
    requirement: NonacademicRequirement, result: RequirementResult, amount: float | None, auto_checked: bool,
) -> ProgressItem:
    checked = result.status == "satisfied"
    return ProgressItem(
        id=requirement.id, name=requirement.name, kind="checklist",
        status=result.status,
        current=result.current, required=requirement.minimum, unit=requirement.unit,
        fraction=float(checked),
        checked=checked, auto_checked=auto_checked,
        recorded_amount=amount,
        reason=result.reason,
        details=result.details, sources=requirement.sources,
    )


def build_progress(
    student_profile: StudentProfile,
    completed_courses: list[Course],
    nonacademic_records: list[NonacademicRecord],
    rules: RuleSource,
    liberal_summary: LiberalSummary | None = None,
) -> ProgressResponse:
    rule = select_rules(student_profile, rules)
    results, entries = evaluate_course_requirements(
        completed_courses, rule, student_profile.admission_year, liberal_summary,
    )
    equivalents = equivalent_codes(rule)
    course_items = [course_item(result) for result in results]
    checklist = [(item, *evaluate_checklist(item, completed_courses, equivalents, nonacademic_records))
                 for item in checklist_requirements(rule)]
    items = course_items + [checklist_item(*row) for row in checklist]
    return ProgressResponse(
        rule_id=rule.id, progress=overall_percent(items), course_progress=overall_percent(course_items),
        items=items, credit_entries=entries,
        notices=rule_notices(rule),
        early_graduation=early_graduation_assessment(rule, student_profile, results + [row[1] for row in checklist]),
    )


def toggle_checklist(
    rule: GraduationRule, records: list[NonacademicRecord], requirement_id: str, checked: bool | None,
    amount: float | None = None, english_test: EnglishTestResult | None = None,
) -> list[NonacademicRecord]:
    """Return the nonacademic records with one checklist item set, cleared, or set to a measured amount.

    `checked=True` stores the requirement minimum (e.g. 700 for TOEIC), so it is satisfied.
    `amount` stores the measured value and is satisfied only when it reaches the minimum.
    """
    requirement = next((item for item in checklist_requirements(rule) if item.id == requirement_id), None)
    if requirement is None:
        raise KeyError(requirement_id)
    kept = [record for record in records if record.requirement_id != requirement_id]
    if amount is not None:
        kept.append(NonacademicRecord(
            requirement_id=requirement_id, amount=amount, status="satisfied", english_test=english_test,
        ))
    elif checked:
        kept.append(NonacademicRecord(requirement_id=requirement_id, amount=requirement.minimum, status="satisfied"))
    return kept
