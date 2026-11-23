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


class StudentRecords(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_courses: list[Course] = Field(default_factory=list, max_length=500)
    nonacademic_records: list[NonacademicRecord] = Field(default_factory=list, max_length=500)


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
    course_name: str | None = None
    semester: str
    transcript_category: str
    recognized_category: Category | None = None
    recognized_credits: Amount = 0
    counts_toward_total: bool = False
    excluded_credits: Amount = 0
    reason: str | None = None
    major_approval_required: bool = False


class RequirementCounts(BaseModel):
    satisfied: int = 0
    unsatisfied: int = 0
    approval_required: int = 0
    verification_required: int = 0


class EvaluationResponse(BaseModel):
    rule_id: str
    dataset: Literal["official", "test"]
    eligible: bool
    status: Status
    progress: float = Field(ge=0, le=100, description="서비스 진행률 (0~100%)")
    progress_policy: Literal["equal_requirement_weight_v1"] = "equal_requirement_weight_v1"
    requirement_counts: RequirementCounts
    requirements: list[RequirementResult]
    credit_entries: list[CreditEntry]
