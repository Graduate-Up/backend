import pytest
from pydantic import ValidationError

from app.schemas.course import Course
from app.schemas.rules import NonacademicRequirement
from app.services.graduation_engine.gpa import compute_gpa, gpa_result

GPA = NonacademicRequirement(id="gpa", name="평점평균", minimum=1.7, sources=[{"title": "test"}])


def make(code, grade, credits=3, semester="2024-1", status="completed"):
    return Course(course_code=code, credits=credits, transcript_category="major",
                  status=status, semester=semester, grade=grade)


def test_weighted_average_on_43_scale_includes_f_and_skips_pass_fail():
    courses = [make("A0000001", "A+"), make("A0000002", "B0"), make("A0000003", "F", status="failed"),
               make("A0000004", "P"), make("A0000005", None)]
    gpa, credits, ungraded = compute_gpa(courses, {})
    assert gpa == 2.43  # (4.3 + 3.0 + 0) * 3 / 9 = 2.433.. -> truncated
    assert credits == 9
    assert ungraded == 1


def test_retake_uses_latest_grade_including_equivalent_codes():
    courses = [make("OLD00001", "F", semester="2022-1", status="failed"), make("NEW00001", "B+", semester="2023-2")]
    assert compute_gpa(courses, {"OLD00001": "NEW00001"})[0] == 3.3
    assert compute_gpa(courses, {})[0] == 1.65


def test_gpa_requirement_status():
    assert gpa_result(GPA, [make("A0000001", "C0")], {}).status == "satisfied"
    low = gpa_result(GPA, [make("A0000001", "D0")], {})
    assert low.status == "unsatisfied" and low.current == 1.0 and low.shortfall == 0.7
    missing = gpa_result(GPA, [make("A0000001", None)], {})
    assert missing.status == "verification_required"


def test_grade_aliases_and_validation():
    assert make("A0000001", "a").grade == "A0"
    assert make("A0000001", "PASS").grade == "P"
    with pytest.raises(ValidationError):
        make("A0000001", "Z+")


def test_gpa_only_counts_when_minimum_is_reached():
    from app.services.graduation_engine.evaluator import requirement_fraction

    # 1.0 is "59% of 1.7" but graduation only asks whether 1.7 is reached, so no partial progress.
    below = gpa_result(GPA, [make("A0000001", "D0")], {})
    assert requirement_fraction(below) == 0
    exactly = gpa_result(GPA, [make("A0000001", "C-")], {})  # 1.7
    assert exactly.status == "satisfied" and requirement_fraction(exactly) == 1
    just_below = gpa_result(GPA, [make("A0000001", "C-", credits=99), make("A0000002", "D+", credits=1)], {})
    assert just_below.current == 1.69 and requirement_fraction(just_below) == 0
