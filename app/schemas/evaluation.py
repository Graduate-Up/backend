from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .course import Amount, Category, Code, Course
from .student import StudentProfile

CreditArea = Literal["total", "major", "liberal_arts"]

Status = Literal["satisfied", "unsatisfied", "approval_required", "verification_required"]
LiberalAreaKey = Literal["독서와토론", "사고교육", "글쓰기", "외국어", "수리", "기초과학", "인문·사회", "자연과학"]
LiberalSummary = dict[LiberalAreaKey, Amount]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str | None = None
    reference: str | None = None


ENGLISH_REQUIREMENT_IDS = {"department-english", "practical-english"}


class EnglishTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    test_type: str = Field(min_length=1, max_length=50, examples=["TOEIC", "OPIc"])
    raw_score: Amount | str = Field(examples=[750, "IM2"])
    taken_month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$", examples=["2026-11"])

    @model_validator(mode="after")
    def valid_raw_score(self):
        if isinstance(self.raw_score, str) and not 1 <= len(self.raw_score) <= 30:
            raise ValueError("영어 시험 원점수 또는 등급을 입력해 주세요.")
        return self


class NonacademicRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    amount: Amount
    status: Status
    english_test: EnglishTestResult | None = None

    @model_validator(mode="after")
    def english_test_only(self):
        if self.english_test is not None and self.requirement_id not in ENGLISH_REQUIREMENT_IDS:
            raise ValueError("영어 요건에만 영어 시험 정보를 저장할 수 있어요.")
        return self


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_profile: StudentProfile
    completed_courses: list[Course]
    nonacademic_records: list[NonacademicRecord] = Field(default_factory=list)
    liberal_summary: LiberalSummary | None = Field(default=None, min_length=8, max_length=8)


class StudentRecords(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_courses: list[Course] = Field(default_factory=list, max_length=500)
    nonacademic_records: list[NonacademicRecord] = Field(default_factory=list, max_length=500)
    liberal_summary: LiberalSummary | None = Field(default=None, min_length=8, max_length=8)


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
    offered_by_computer_science: bool | None = None
    recognized_category: Category | None = None
    recognized_credits: Amount = 0
    counts_toward_total: bool = False
    excluded_credits: Amount = 0
    reason: str | None = None
    major_approval_required: bool = False
    course_areas: list[str] = Field(
        default_factory=list,
        description="입학년도 교육과정표·수강 학기 교양 영역 기준 구분. 예: 기본소양, 공학전공, 설계, "
                    "첨성인기초(글쓰기), 첨성인핵심(인문·사회), 첨성인일반, SDG, 창업교과목",
    )


class RequirementCounts(BaseModel):
    satisfied: int = 0
    unsatisfied: int = 0
    approval_required: int = 0
    verification_required: int = 0


class ProgressNotice(BaseModel):
    id: str
    name: str
    sources: list[Source]


class EarlyGraduationPolicy(BaseModel):
    min_semesters: int = Field(ge=1)
    min_gpa: float = Field(ge=0, le=4.3)
    rule: str
    not_for_transfer: bool
    sources: list[Source]


class EarlyGraduationAssessment(BaseModel):
    status: Literal["criteria_met", "criteria_not_met", "verification_required", "not_applicable"]
    registered_semesters: int | None
    gpa: Amount | None
    graduation_requirements_met: bool
    policy: EarlyGraduationPolicy


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
    notices: list[ProgressNotice] = Field(default_factory=list)
    early_graduation: EarlyGraduationAssessment | None = None


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
    recorded_amount: Amount | None = Field(default=None, description="checklist 항목에 저장된 수치 (없으면 null)")
    reason: str
    details: list[str] = Field(default_factory=list)
    credit_area: CreditArea | None = None
    required_courses: list[RequiredCourseResult] = Field(default_factory=list)
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
    early_graduation: EarlyGraduationAssessment | None = None


class ChecklistUpdate(BaseModel):
    """체크박스(`checked`) 또는 실제 수치(`amount`, 예: TOEIC 점수, 상담 횟수) 중 하나로 저장한다.

    `checked: true`는 기준값(예: 700점, 8회)으로 저장되어 충족이다. `amount`는 그대로 저장되며
    기준 이상일 때만 충족이다. `checked: false`는 기록을 지운다.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1, max_length=200)
    checked: bool | None = None
    amount: Amount | None = Field(default=None, description="실제 수치. 기준 이상이면 충족")
    english_test: EnglishTestResult | None = None

    @model_validator(mode="after")
    def one_value(self):
        if (self.checked is None) == (self.amount is None):
            raise ValueError("checked 또는 amount 중 하나만 보내 주세요.")
        if self.english_test is not None and (self.amount is None or self.requirement_id not in ENGLISH_REQUIREMENT_IDS):
            raise ValueError("영어 시험 정보는 영어 요건의 amount와 함께 보내 주세요.")
        return self
