from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(str.upper)]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Category = Literal["major", "liberal_arts", "general"]
MAJOR_TRANSCRIPT_CATEGORIES = ("전공", "전공필수", "전공선택")

# 경북대 4.3 만점 등급별 평점. P/NP는 평점 계산에서 제외한다.
GRADE_POINTS = {
    "A+": 4.3, "A0": 4.0, "A-": 3.7, "B+": 3.3, "B0": 3.0, "B-": 2.7,
    "C+": 2.3, "C0": 2.0, "C-": 1.7, "D+": 1.3, "D0": 1.0, "D-": 0.7, "F": 0.0,
}
GRADE_ALIASES = {
    "A": "A0", "B": "B0", "C": "C0", "D": "D0",
    "P": "P", "S": "P", "PASS": "P", "합격": "P", "이수": "P",
    "NP": "NP", "U": "NP", "FAIL": "NP", "불합격": "NP", "미이수": "NP",
}


def normalize_grade(value: str | None) -> str | None:
    """Return a canonical grade (A+..F, P, NP) or None when the text is not a grade."""
    if value is None:
        return None
    text = value.strip().upper().replace(" ", "")
    if not text:
        return None
    text = GRADE_ALIASES.get(text, text)
    return text if text in GRADE_POINTS or text in {"P", "NP"} else None


def _grade(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    grade = normalize_grade(value)
    if grade is None:
        raise ValueError("성적등급은 A+, A0, A-, … D-, F, P, NP 중 하나여야 합니다.")
    return grade


Grade = Annotated[str | None, AfterValidator(_grade)]


class Course(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: Code
    course_name: str | None = Field(default=None, min_length=1, max_length=200)
    semester: str = Field(min_length=1)
    credits: Amount
    transcript_category: str = Field(min_length=1)
    offered_by_computer_science: bool | None = None
    prior_institution: bool | None = None
    grade: Grade = Field(default=None, max_length=10, description="성적등급. 평점평균 계산에 사용")
    status: Literal["completed", "in_progress", "failed", "incomplete"]
