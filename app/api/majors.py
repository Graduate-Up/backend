from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas.student import Major
from app.services.graduation_engine.rule_selector import MAJOR_NAMES, ProfileError, available_majors
from app.services.recommendations import ProgramSummary, programs_catalog

router = APIRouter(tags=["majors"])


class MajorOption(BaseModel):
    code: Major
    name: str


class MajorsResponse(BaseModel):
    admission_year: int
    majors: list[MajorOption]


@router.get("/convergence-programs")
def get_convergence_programs() -> list[ProgramSummary]:
    return [program for program in programs_catalog() if program.kind == "convergence"]


@router.get("/majors")
def get_majors(admission_year: Annotated[int, Query(ge=1900, le=2200)]) -> MajorsResponse:
    try:
        codes = available_majors(admission_year)
    except ProfileError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return MajorsResponse(admission_year=admission_year, majors=[
        MajorOption(code=code, name=MAJOR_NAMES[code]) for code in codes
    ])
