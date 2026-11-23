import json
import re
from collections.abc import Iterable
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field

COURSE_CODE = re.compile(r"(?<![A-Z0-9])[A-Z]{4}[0-9]{4}(?![A-Z0-9])", re.IGNORECASE)
PASS_GRADE = re.compile(r"(?:[ABCD][+0-]?|P|S|PASS|합격|이수)")
FAIL_GRADES = {"F", "U", "NP", "FAIL", "불합격", "미이수"}
TERMS = {
    "1": "1", "1학기": "1", "제1학기": "1", "2": "2", "2학기": "2", "제2학기": "2",
    "하계": "summer", "여름": "summer", "summer": "summer",
    "동계": "winter", "겨울": "winter", "winter": "winter",
}
TERM_ORDER = {"1": 1, "summer": 2, "2": 3, "winter": 4}


class TranscriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=250_000)


class TranscriptCourse(BaseModel):
    course_code: str
    course_name: str | None = None
    credits: float | None = None
    semester: str | None = None
    semester_label: str | None = None
    transcript_category: str | None = None
    completed: bool = False


class TranscriptPreview(BaseModel):
    courses: list[TranscriptCourse]
    excluded_failed: int
    duplicates_removed: int
    needs_review: int
    completed_credits: float


class TranscriptImportInput(TranscriptInput):
    expected: int = Field(ge=1, le=500)


def field(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            cleaned = str(value).strip()
            if cleaned:
                return cleaned
    return ""


def _rows(text: str) -> Iterable[dict]:
    if text.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise ValueError("JSON 형식이 올바르지 않습니다.") from exc
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("과목 행을 담은 data 배열을 붙여넣어 주세요.")
        return rows

    lines = [line.split("\t") for line in text.splitlines()]
    for index, line in enumerate(lines):
        headers = [column.strip() for column in line]
        if "교과목코드" in headers and "학점" in headers:
            return [dict(zip(headers, cells)) for cells in lines[index + 1:] if len(cells) == len(headers)]
    return [{"sbjetCd": match.group().upper()} for match in COURSE_CODE.finditer(text)]


def _semester(row: dict) -> tuple[str | None, str | None]:
    year = field(row, "년도", "연도", "year", "yy", "yearCd", "schYear")
    term = field(row, "학기", "semester", "semesterNm", "smstNm", "smtNm", "smst", "smt")
    label = " ".join(part for part in (year, term) if part)[:100] or None
    year = re.sub(r"\s*(?:학년도|년도|년)$", "", year)
    term = re.sub(r"\s+", "", term).lower()
    combined = re.fullmatch(r"((?:19|20|21)[0-9]{2})(?:학년도|년도|년|[-./])(.+)", term)
    if combined and (not year or year == combined[1]):
        year, term = combined.groups()
    if "계절" in term or "디딤돌" in term or "학기" in term:
        seasons = [value for names, value in ((("하계", "여름"), "summer"), (("동계", "겨울"), "winter"))
                   if any(name in term for name in names)]
        if len(seasons) == 1:
            term = seasons[0]
    if re.fullmatch(r"(?:19|20|21)[0-9]{2}", year) and term in TERMS:
        return year + "-" + TERMS[term], None
    return None, label


def _priority(course: TranscriptCourse) -> tuple[bool, str, int]:
    year, _, term = (course.semester or "").partition("-")
    return course.completed, year, TERM_ORDER.get(term, 0)


def parse_transcript(text: str) -> TranscriptPreview:
    courses: dict[str, TranscriptCourse] = {}
    failed = duplicates = 0
    for index, row in enumerate(_rows(text)):
        if index >= 500:
            raise ValueError("한 번에 500행까지 불러올 수 있습니다.")
        code = field(row, "sbjetCd", "교과목코드").upper()
        if not COURSE_CODE.fullmatch(code):
            continue
        grade = field(row, "성적등급", "grade", "gradeNm", "grd", "grdNm", "grde", "grdCd", "gradeCd").upper()
        if grade in FAIL_GRADES:
            failed += 1
            continue
        value = field(row, "crdit", "학점")
        credits = float(value) if re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3})?", value) else None
        if credits is not None and not isfinite(credits):
            credits = None
        semester, semester_label = _semester(row)
        course = TranscriptCourse(
            course_code=code,
            course_name=field(row, "sbjetNm", "교과목명")[:200] or None,
            credits=credits,
            semester=semester,
            semester_label=semester_label,
            transcript_category=field(row, "교과목구분", "sbjetDvNm", "sbjetDivNm", "crseDivNm", "category")[:100] or None,
            completed=bool(PASS_GRADE.fullmatch(grade)),
        )
        previous = courses.get(code)
        if previous is not None:
            duplicates += 1
        if previous is None or _priority(course) > _priority(previous):
            courses[code] = course

    if not courses and not failed:
        raise ValueError("교과목코드를 찾지 못했습니다. 전체성적 표나 과목 data 배열을 붙여넣어 주세요.")
    result = list(courses.values())
    return TranscriptPreview(
        courses=result, excluded_failed=failed, duplicates_removed=duplicates,
        needs_review=sum(not (item.completed and item.semester and item.transcript_category and item.credits is not None)
                         for item in result),
        completed_credits=sum(item.credits or 0 for item in result if item.completed),
    )
