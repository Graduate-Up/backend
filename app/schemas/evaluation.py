from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .course import Amount, Category, Code, Course
from .student import StudentProfile

CreditArea = Literal["total", "major", "liberal_arts"]

Status = Literal["satisfied", "unsatisfied", "approval_required", "verification_required"]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str | None = None
    reference: str | None = None


class NonacademicRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    amount: Amount
    status: Status


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_profile: StudentProfile
    completed_courses: list[Course]
    nonacademic_records: list[NonacademicRecord] = Field(default_factory=list)


class RequiredCourseResult(BaseModel):
    course_code: Code
    course_name: str
    status: Literal["completed", "in_progress", "missing", "approval_required", "verification_required"]


class RequirementResult(BaseModel):
    id: str
    name: str
    status: Status
    current: Amount
    required: Amount
    shortfall: Amount
    reason: str
    sources: list[Source]
    credit_area: CreditArea | None = None
    required_courses: list[RequiredCourseResult] = Field(default_factory=list)


class CreditEntry(BaseModel):
    course_code: Code
    semester: str
    transcript_category: str
    recognized_category: Category | None = None
    recognized_credits: Amount = 0
    counts_toward_total: bool = False
    excluded_credits: Amount = 0
    reason: str | None = None


class EvaluationResponse(BaseModel):
    rule_id: str
    dataset: Literal["official", "test"]
    eligible: bool
    status: Status
    progress: float | None = None
    requirements: list[RequirementResult]
    credit_entries: list[CreditEntry]
