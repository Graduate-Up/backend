from collections import defaultdict
from decimal import Decimal

from app.schemas.course import Course
from app.schemas.evaluation import CreditEntry, RequirementResult
from app.schemas.rules import CreditTreatment, GraduationRule
from app.services.course_metadata import computer_science_offering


# 성적표 교과구분이 이 값이면 학부 개설 과목이어도 주전공 학점으로 보지 않는다 (다전공·현장실습 등).
NON_MAJOR_CATEGORIES = {"다중전공", "현장실습", "해외대학", "대학원", "융합전공", "부전공", "복수전공", "연계전공", "다전공"}


def check_credits(
    courses: list[Course], rule: GraduationRule, equivalents: dict[str, str],
) -> tuple[list[RequirementResult], list[CreditEntry]]:
    entries = [
        CreditEntry(course_code=c.course_code, course_name=c.course_name,
                    semester=c.semester, transcript_category=c.transcript_category,
                    offered_by_computer_science=computer_science_offering(c.course_code, c.offered_by_computer_science))
        for c in courses
    ]
    totals = defaultdict(Decimal)
    pending = defaultdict(set)
    cap_used = [Decimal(0) for cap in rule.credit_caps]
    counted = set()
    transfer_used = Decimal(0)
    transfer_limit = None if rule.transfer_credit_max is None else Decimal(str(rule.transfer_credit_max))
    completed_codes = {course.course_code for course in courses if course.status == "completed"}
    uncertain_retake_codes = {
        code for old, aliases in rule.review_equivalences.items() for new in aliases
        if {old, new} <= completed_codes for code in (old, new)
    }
    completed_attempts = defaultdict(list)
    for course in courses:
        if course.status == "completed":
            completed_attempts[equivalents.get(course.course_code, course.course_code)].append(course)

    def treatment_for(course: Course, canonical: str) -> tuple[CreditTreatment | None, str | None]:
        if transfer_limit is not None and course.prior_institution is True:
            category = "liberal_arts" if course.transcript_category.strip() in {
                "교양", "교양필수", "교양선택",
            } else "general"
            return CreditTreatment(category=category), None
        explicit = rule.course_treatments.get(course.course_code) or rule.course_treatments.get(canonical)
        if explicit is not None:
            return explicit, None
        for prefix, treatment in rule.prefix_treatments.items():
            if canonical.startswith(prefix):
                return treatment, None

        category = course.transcript_category.strip()
        treatment = rule.transcript_categories.get(category)
        if category in NON_MAJOR_CATEGORIES:
            return treatment, None
        general = CreditTreatment(category="general")
        department = computer_science_offering(course.course_code, course.offered_by_computer_science)
        if category in rule.major_transcript_categories:
            return (general if department is False else CreditTreatment(category="major")), None
        if canonical not in rule.department_major_codes:
            return treatment, None
        if department is False:
            return general, None
        if department is True:
            return treatment or general, "approval_required"
        return treatment or general, None
    cap_codes = [
        {equivalents.get(code, code) for code in cap.course_codes} if cap.course_codes is not None else None
        for cap in rule.credit_caps
    ]

    # Apply caps in a stable order and retain one entry for each equivalent course group.
    order = sorted(range(len(courses)), key=lambda i: (
        courses[i].credits, courses[i].semester, courses[i].course_code, courses[i].transcript_category,
    ), reverse=True)
    for index in order:
        course, entry = courses[index], entries[index]
        entry.excluded_credits = course.credits
        if course.status != "completed":
            entry.reason = course.status
            continue
        if course.course_code in uncertain_retake_codes or (
            transfer_limit is not None and course.prior_institution is None
            and entry.offered_by_computer_science is not True
        ):
            entry.reason = "verification_required"
            for area in ("total", "major", "liberal_arts"):
                pending[area].add(entry.reason)
            continue
        canonical = equivalents.get(course.course_code, course.course_code)
        if canonical in counted:
            entry.reason = "duplicate_course"
            continue
        counted.add(canonical)

        treatment, major_pending = treatment_for(course, canonical)
        conflicting_attempts = any(
            attempt.credits != course.credits or treatment_for(attempt, canonical) != (treatment, major_pending)
            for attempt in completed_attempts[canonical]
        )
        if treatment is None or conflicting_attempts:
            entry.reason = "verification_required"
            for area in ("total", "major", "liberal_arts"):
                pending[area].add(entry.reason)
            continue

        if major_pending:
            pending["major"].add(major_pending)
            entry.reason = major_pending
            entry.major_approval_required = major_pending == "approval_required"
        areas = [treatment.category]
        if treatment.counts_toward_total:
            areas.append("total")
        if treatment.status != "recognized":
            entry.reason = treatment.status
            entry.major_approval_required = treatment.category == "major" and treatment.status == "approval_required"
            for area in set(areas).intersection({"total", "major", "liberal_arts"}) or {"total"}:
                pending[area].add(treatment.status)
            continue

        entry.recognized_category = treatment.category
        entry.counts_toward_total = treatment.counts_toward_total
        matching_caps = [
            i for i, cap in enumerate(rule.credit_caps)
            if (cap.category is None or cap.category == treatment.category)
            and (cap_codes[i] is None or canonical in cap_codes[i])
            and (cap.code_prefix is None or canonical.startswith(cap.code_prefix))
        ]
        original = Decimal(str(course.credits))
        credits = min([original] + [Decimal(str(rule.credit_caps[i].maximum)) - cap_used[i]
                                    for i in matching_caps])
        if transfer_limit is not None and course.prior_institution is True:
            credits = min(credits, transfer_limit - transfer_used)
            transfer_used += credits
        for i in matching_caps:
            cap_used[i] += credits
        for area in areas:
            totals[area] += credits
        entry.recognized_credits = float(credits)
        entry.excluded_credits = float(original - credits)
        if credits < original:
            entry.reason = "credit_cap"
        elif not treatment.counts_toward_total:
            entry.reason = "excluded_from_total"

    results = []
    for requirement in rule.credit_requirements:
        current = totals[requirement.area]
        shortfall = max(Decimal(0), Decimal(str(requirement.minimum)) - current)
        if "verification_required" in pending[requirement.area]:
            status = "verification_required"
        elif "approval_required" in pending[requirement.area]:
            status = "approval_required"
        else:
            status = "unsatisfied" if shortfall else "satisfied"
        results.append(RequirementResult(
            id=requirement.id, name=requirement.name, status=status,
            current=float(current), required=requirement.minimum, shortfall=float(shortfall),
            reason="Credit classification or approval is pending" if pending[requirement.area]
            else "Calculated from recognized completed credits",
            sources=requirement.sources, credit_area=requirement.area,
        ))
    return results, entries
