from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.config import RULE_DIRECTORY
from app.schemas.evaluation import Source
from app.schemas.student import Major, StudentProfile
from app.services.course_metadata import (
    ABEEK_PREREQUISITE_SOURCE, ABEEK_PREREQUISITES, CAREER_TAGS, SIMCOM_RECOMMENDED_ORDER,
    SIMCOM_RECOMMENDATION_SOURCE, CareerTag, computer_science_offering,
)
from app.services.graduation_engine.bundle import matching_entries
from app.services.graduation_engine.course_areas import curriculum_is_sourced, curriculum_table
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
    offered_by_computer_science: bool | None = None
    career_tags: list[CareerTag]
    prerequisite_groups: list[list[str]] | None = None
    prerequisite_kind: Literal["required", "recommended"] | None = None
    prerequisite_sources: list[Source]


class CourseSearchResponse(BaseModel):
    source: Literal["local_curriculum"] = "local_curriculum"
    basis: str
    inferred: bool
    curriculum_source: Source
    total: int
    items: list[CatalogCourse]


@router.get("/courses")
def search_courses(
    admission_year: Annotated[int, Query(ge=1900, le=2200)],
    major: Major,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    abeek: bool | None = None,
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
        table = curriculum_table(major, admission_year)
        inferred = not curriculum_is_sourced(major, admission_year)
        term = "".join(q.split()).casefold()
        matches = [
            row for row in table.courses.values()
            if term in row.code.casefold() or term in "".join(row.name.split()).casefold()
        ]
        items = []
        for row in matches[offset:offset + limit]:
            code = row.code
            prerequisites, kind, sources = None, None, []
            if major == "plsop" and abeek is True and code in ABEEK_PREREQUISITES:
                prerequisites, kind, sources = ABEEK_PREREQUISITES[code], "required", [ABEEK_PREREQUISITE_SOURCE]
            elif major == "simcom" and code in SIMCOM_RECOMMENDED_ORDER:
                prerequisites, kind, sources = SIMCOM_RECOMMENDED_ORDER[code], "recommended", [SIMCOM_RECOMMENDATION_SOURCE]
            items.append(CatalogCourse(
                course_code=code, course_name=row.name, credits=row.credits,
                curriculum_category=row.category, recommended_year=row.grade_year or None,
                recommended_semester=row.semester or None, note=row.note,
                offered_by_computer_science=computer_science_offering(code),
                career_tags=CAREER_TAGS.get(code, []), prerequisite_groups=prerequisites,
                prerequisite_kind=kind, prerequisite_sources=sources,
            ))
        return CourseSearchResponse(
            basis=table.source.title + (f" · {admission_year}학번 적용 추정" if inferred else ""),
            inferred=inferred, curriculum_source=table.source, total=len(matches), items=items,
        )
    except (OSError, KeyError, ValueError) as exc:
        raise RuleDataError("과목 카탈로그 파일을 읽을 수 없습니다.") from exc
