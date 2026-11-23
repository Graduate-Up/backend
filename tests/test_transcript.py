import json

import pytest

from app.services.transcript import parse_transcript


def test_portal_json_only_returns_course_data():
    preview = parse_transcript(json.dumps({"data": [
        {"sbjetCd": "ELEC0462", "sbjetNm": "Systems", "crdit": 3, "year": 2023, "semester": 2, "grade": "A+"},
        {"sbjetCd": "ELEC0462", "sbjetNm": "Systems", "crdit": 3, "year": 2024, "semester": 1, "grade": "B0",
         "studentName": "Example private name", "gpa": 3.2},
        {"sbjetCd": "COMP0217", "crdit": 3, "grade": "F"},
        {"sbjetCd": "COMP0312", "crdit": 3, "grade": "U"},
    ]}))
    assert preview.excluded_failed == 2
    assert preview.duplicates_removed == 1
    assert preview.completed_credits == 3
    assert preview.courses[0].semester == "2024-1"
    assert preview.courses[0].grade == "B0"
    assert not {"gpa", "studentName"} & preview.courses[0].model_dump().keys()
    # F is kept for the GPA (F counts as 0); U is a pass/fail result and is not.
    assert [item.course_code for item in preview.failed_courses] == ["COMP0217"]
    assert preview.failed_courses[0].grade == "F"


def test_failed_course_retaken_and_passed_is_not_kept_as_failed():
    preview = parse_transcript(json.dumps({"data": [
        {"sbjetCd": "COMP0217", "crdit": 3, "year": 2022, "semester": 1, "grade": "F"},
        {"sbjetCd": "COMP0217", "crdit": 3, "year": 2023, "semester": 1, "grade": "B+"},
    ]}))
    assert preview.failed_courses == []
    assert preview.courses[0].grade == "B+"


def test_visible_table_preserves_transcript_category():
    text = "년도\t학기\t교과목구분\t교과목코드\t교과목명\t학점\t성적등급\t점수\n"
    text += "2024\t1학기\t교양\tCLTR0819\t기초수학2\t3\tB+\t3.3"
    result = parse_transcript(text)
    assert result.needs_review == 0
    assert result.courses[0].transcript_category == "교양"
    assert result.courses[0].completed


def test_free_text_only_extracts_codes_without_claiming_completion():
    result = parse_transcript("Courses: COME0331 and ELEC0462")
    assert [item.course_code for item in result.courses] == ["COME0331", "ELEC0462"]
    assert result.needs_review == 2
    assert result.completed_credits == 0


def test_invalid_payload():
    with pytest.raises(ValueError, match="data"):
        parse_transcript('{"data": null}')


@pytest.mark.parametrize(("year", "term", "expected"), [
    ("2024학년도", "제 1 학기", "2024-1"),
    ("2024", "계절학기(하계)", "2024-summer"),
    ("2024", "디딤돌학기(동계)", "2024-winter"),
    ("", "2024학년도 제2학기", "2024-2"),
    ("2024", "2025-2", None),
])
def test_semester_variants(year, term, expected):
    preview = parse_transcript(json.dumps({"data": [{
        "sbjetCd": "TEST0123", "crdit": 3, "year": year, "semester": term,
        "grade": "PASS", "category": "major",
    }]}))
    assert preview.courses[0].semester == expected
    if expected is None:
        assert preview.courses[0].semester_label == f"{year} {term}"
