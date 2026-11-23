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
    details: list[str] = Field(default_factory=list, description="판정 근거 (인정된 과목, 확인이 필요한 과목 등)")


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


class ProgressItem(BaseModel):
    id: str
    name: str
    kind: Literal["course", "checklist"] = Field(
        description="course: 서버가 이수 과목으로 계산 / checklist: 프런트에서 체크해 충족 여부를 정함",
    )
    status: Status
    current: Amount
    required: Amount
    unit: str | None = None
    fraction: float = Field(ge=0, le=1, description="이 항목의 진행 비율. checklist는 checked면 1, 아니면 0")
    checked: bool | None = Field(default=None, description="checklist 항목의 저장된 체크 상태. course 항목은 null")
    auto_checked: bool = Field(default=False, description="이수 과목으로 충족이 확인된 checklist 항목 (예: SW 대체 과목 이수)")
    reason: str
    details: list[str] = Field(default_factory=list)
    credit_area: CreditArea | None = None
    required_courses: list[RequiredCourseResult] = Field(default_factory=list)
    sources: list[Source]


class ProgressNotice(BaseModel):
    id: str
    name: str
    sources: list[Source]


class ProgressResponse(BaseModel):
    rule_id: str
    progress: float = Field(ge=0, le=100, description="course 항목 + 저장된 checklist 체크 상태로 계산한 전체 진행률")
    course_progress: float = Field(ge=0, le=100, description="이수 과목으로 계산되는 course 항목만의 진행률")
    progress_policy: Literal["equal_item_weight_v2"] = "equal_item_weight_v2"
    items: list[ProgressItem]
    notices: list[ProgressNotice] = Field(
        default_factory=list, description="규칙 해석 확인이 필요한 항목. 진행률에는 포함하지 않음",
    )
    credit_entries: list[CreditEntry]


class ChecklistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1, max_length=200)
    checked: bool
