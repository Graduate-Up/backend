from app.schemas.rules import Conditions, GraduationRule
from app.schemas.student import StudentProfile

from .bundle import BundleProfileError, BundleUnavailableError, RuleBundle
from .rule_loader import RuleDataError, RuleUnavailableError

RuleSource = list[GraduationRule] | RuleBundle

MAJOR_NAMES = {
    "glsop": "글로벌SW융합",
    "plsop": "플랫폼SW&데이터과학",
    "aicom": "인공지능컴퓨팅",
    "simcom": "심화컴퓨팅",
    "chumcom": "첨단컴퓨팅연구",
}


class ProfileError(ValueError):
    pass


def available_majors(year: int) -> list[str]:
    if 2017 <= year <= 2022:
        return ["glsop", "plsop"]
    if 2023 <= year <= 2025:
        return ["glsop", "plsop", "aicom"]
    if year == 2026:
        return ["aicom", "simcom", "chumcom"]
    raise ProfileError("Admission year is outside the supported range (2017–2026)")


def validate_major(student: StudentProfile) -> None:
    if student.major not in available_majors(student.admission_year):
        raise ProfileError("Major is not available for this admission year")


def matches(student: StudentProfile, condition: Conditions) -> bool:
    if (
        (condition.admission_from is not None and student.admission_year < condition.admission_from)
        or (condition.admission_to is not None and student.admission_year > condition.admission_to)
        or (condition.disabled_student is not None and student.disabled_student != condition.disabled_student)
        or (condition.abeek_values is not None and student.abeek not in condition.abeek_values)
        or (condition.tracks is not None and student.track not in condition.tracks)
        or (condition.multi_majors is not None and student.multi_major not in condition.multi_majors)
        or (condition.transfer_student is not None and student.transfer_student != condition.transfer_student)
        or (condition.international_student is not None
            and student.international_student != condition.international_student)
    ):
        return False

    graduation = student.expected_graduation_date
    if graduation is not None and (
        (condition.graduation_from is not None and graduation < condition.graduation_from.isoformat()[:7])
        or (condition.graduation_to is not None and graduation > condition.graduation_to.isoformat()[:7])
    ):
        return False
    if condition.abeek is not None and student.abeek is not None and student.abeek != condition.abeek:
        return False
    if graduation is None and (condition.graduation_from is not None or condition.graduation_to is not None):
        raise ProfileError("expected_graduation_date is required for this rule")
    if condition.abeek is not None and student.abeek is None:
        raise ProfileError("abeek is required for this rule")
    return True


def candidate_rules(student: StudentProfile, rules: list[GraduationRule]) -> list[GraduationRule]:
    validate_major(student)
    candidates = [
        rule for rule in rules
        if rule.major == student.major and rule.admission_from <= student.admission_year <= rule.admission_to
    ]
    if not candidates:
        raise RuleUnavailableError("No graduation rules for this major and admission year")
    return candidates


def select_rules(student_profile: StudentProfile, rules: RuleSource) -> GraduationRule:
    student = student_profile
    if isinstance(rules, RuleBundle):
        validate_major(student)
        try:
            return rules.resolve(student)
        except BundleProfileError as exc:
            raise ProfileError(str(exc)) from exc
        except BundleUnavailableError as exc:
            raise RuleUnavailableError("No graduation rules for this major and admission year") from exc
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            raise RuleDataError("Invalid graduation rules bundle") from exc
    candidates = candidate_rules(student, rules)
    track_rules = [rule for rule in candidates if student.track in rule.allowed_tracks]
    if not track_rules:
        raise ProfileError("Track is not available for this major and admission year")
    selected = [rule for rule in track_rules if matches(student, rule.conditions)]
    if not selected:
        raise ProfileError("No graduation rule matches this student profile")
    if len(selected) != 1:
        raise RuleDataError("Multiple graduation rules match this student profile")

    major_rule = selected[0]
    applicable = [major_rule] + [
        rule for rule in rules
        if rule.major == "common" and rule.dataset == major_rule.dataset
        and rule.admission_from <= student.admission_year <= rule.admission_to
        and matches(student, rule.conditions)
    ]
    fields = {}
    for key in ("credit_requirements", "course_requirements", "nonacademic_requirements", "verification_requirements"):
        fields[key] = [
            item.model_copy(update={"sources": item.sources or rule.sources})
            for rule in applicable for item in getattr(rule, key)
            if matches(student, item.conditions)
        ]
    credit_areas = [item.area for item in fields["credit_requirements"]]
    if sorted(credit_areas) != ["liberal_arts", "major", "total"]:
        raise RuleDataError("Expected one applicable minimum for each credit area")
    payload = major_rule.model_dump()
    payload.update(fields)
    try:
        return GraduationRule.model_validate(payload)
    except ValueError as exc:
        raise RuleDataError("Selected rule has conflicting requirements") from exc
