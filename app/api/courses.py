from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.config import RULE_DIRECTORY
from app.schemas.student import Major, StudentProfile
from app.services.graduation_engine.bundle import find_catalog, matching_entries
from app.services.graduation_engine.rule_loader import RuleDataError, RuleUnavailableError
from app.services.graduation_engine.rule_selector import validate_major

router = APIRouter(tags=["courses"])


class CatalogCourse(BaseModel):
    course_code: str
    course_name: str
    credits: float
    curriculum_category: str
    recommended_year: str | None = None
    recommended_semester: str | None = None
    note: str | None = None


class CourseSearchResponse(BaseModel):
    source: Literal["local_curriculum"] = "local_curriculum"
    basis: str
    inferred: bool
    total: int
    items: list[CatalogCourse]


@router.get("/courses")
def search_courses(
    admission_year: Annotated[int, Query(ge=1900, le=2200)],
    major: Major,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CourseSearchResponse:
    student = StudentProfile(admission_year=admission_year, major=major)
    validate_major(student)
    root = RULE_DIRECTORY.parents[1] / "jolup_rules_all" / "app" / "rules"
    if not (root / "index.json").is_file():
        raise RuleUnavailableError("과목 카탈로그를 사용할 수 없습니다.")
    try:
        entries = matching_entries(student)
        if not entries:
            raise RuleUnavailableError("해당 학번과 전공의 과목 카탈로그가 없습니다.")
        curriculum, rows = find_catalog(entries)
        term = "".join(q.split()).casefold()
        matches = [
            row for row in rows
            if term in row["code"].casefold() or term in "".join(row["name"].split()).casefold()
        ]
        items = [
            CatalogCourse(
                course_code=row["code"], course_name=row["name"], credits=row["credits"],
                curriculum_category=row["category"], recommended_year=row.get("year") or None,
                recommended_semester=row.get("semester") or None, note=row.get("note") or None,
            ) for row in matches[offset:offset + limit]
        ]
        return CourseSearchResponse(
            basis=curriculum["basis"], inferred=curriculum.get("inferred", False), total=len(matches), items=items,
        )
    except (OSError, KeyError, ValueError) as exc:
        raise RuleDataError("과목 카탈로그 파일을 읽을 수 없습니다.") from exc
