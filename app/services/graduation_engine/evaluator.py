from app.schemas.course import Course
from app.schemas.evaluation import EvaluationResponse, NonacademicRecord, RequirementResult
from app.schemas.student import StudentProfile

from .credit_checker import check_credits
from .required_course_checker import check_required_courses
from .rule_selector import RuleSource, select_rules


def evaluate_graduation(
    student_profile: StudentProfile,
    completed_courses: list[Course],
    nonacademic_records: list[NonacademicRecord],
    rules: RuleSource,
) -> EvaluationResponse:
    rule = select_rules(student_profile, rules)
    equivalents = {alias: code for code, aliases in rule.equivalent_courses.items() for alias in aliases}
    requirements, entries = check_credits(completed_courses, rule, equivalents)
    requirements.extend(check_required_courses(completed_courses, item, equivalents)
                        for item in rule.course_requirements)

    for item in rule.nonacademic_requirements:
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

    states = {item.status for item in requirements}
    status = next((state for state in ("verification_required", "approval_required", "unsatisfied")
                   if state in states), "satisfied")
    return EvaluationResponse(
        rule_id=rule.id, dataset=rule.dataset, eligible=status == "satisfied", status=status,
        requirements=requirements, credit_entries=entries,
    )
