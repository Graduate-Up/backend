from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .course import Amount, Category, Code
from .evaluation import CreditArea, Source
from .student import Major, MultiMajor

Identifier = Annotated[str, Field(min_length=1)]


class RuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Conditions(RuleModel):
    admission_from: int | None = None
    admission_to: int | None = None
    graduation_from: date | None = None
    graduation_to: date | None = None
    tracks: list[str | None] | None = Field(default=None, min_length=1)
    abeek: bool | None = None
    abeek_values: list[bool | None] | None = Field(default=None, min_length=1)
    disabled_student: bool | None = None
    multi_majors: list[MultiMajor] | None = Field(default=None, min_length=1)
    transfer_student: bool | None = None
    international_student: bool | None = None

    @model_validator(mode="after")
    def validate_ranges(self):
        for start, end in (
            (self.admission_from, self.admission_to),
            (self.graduation_from, self.graduation_to),
        ):
            if start is not None and end is not None and start > end:
                raise ValueError("Condition start must be at or before its end")
        return self


class Requirement(RuleModel):
    id: Identifier
    name: Identifier
    conditions: Conditions = Field(default_factory=Conditions)
    sources: list[Source] = Field(default_factory=list)


class CreditRequirement(Requirement):
    area: CreditArea
    minimum: Amount


class CourseOption(RuleModel):
    course_code: Code
    course_name: Identifier
    substitutes: list[Code] = Field(default_factory=list)


class CourseRequirement(Requirement):
    courses: list[CourseOption] = Field(min_length=1)
    choose: int = Field(ge=1)
    required_transcript_category: str | None = None
    require_department: bool = False
    reject_transfer_courses: bool = False
    minimum_course_credits: Amount = 0
    accept_equivalent_codes: bool = True
    match_by_course_name: bool = False

    @model_validator(mode="after")
    def validate_options(self):
        if self.choose > len(self.courses):
            raise ValueError("choose cannot exceed the number of options")
        codes = [course.course_code for course in self.courses]
        if len(set(codes)) != len(codes):
            raise ValueError("Duplicate required course options")
        return self


class NonacademicRequirement(Requirement):
    minimum: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1
    unit: str | None = None
    details: list[str] = Field(default_factory=list)
    course_credits: dict[Code, Amount] = Field(
        default_factory=dict, description="이 요건에 학점으로 인정되는 과목 (예: ABEEK 설계 과목별 설계학점)",
    )
    course_codes: list[Code] = Field(
        default_factory=list, description="이수하면 이 요건이 충족되는 과목 (예: SW 졸업자격인정 대체 과목)",
    )


class VerificationRequirement(Requirement):
    pass


class CreditTreatment(RuleModel):
    category: Category
    status: Literal["recognized", "approval_required", "verification_required"] = "recognized"
    counts_toward_total: bool = True


class CreditCap(RuleModel):
    maximum: Amount
    category: Category | None = None
    course_codes: list[Code] | None = Field(default=None, min_length=1)
    code_prefix: Code | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        if self.category is None and self.course_codes is None and self.code_prefix is None:
            raise ValueError("A credit cap needs a category or course codes")
        return self


class GraduationRule(RuleModel):
    id: Identifier
    dataset: Literal["official", "test"]
    major: Major | Literal["common"]
    admission_from: int
    admission_to: int
    allowed_tracks: list[str | None] = Field(default_factory=lambda: [None], min_length=1)
    conditions: Conditions = Field(default_factory=Conditions)
    sources: list[Source] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)
    credit_requirements: list[CreditRequirement]
    course_requirements: list[CourseRequirement]
    nonacademic_requirements: list[NonacademicRequirement]
    verification_requirements: list[VerificationRequirement] = Field(default_factory=list)
    department_major_codes: list[Code] = Field(default_factory=list)
    major_transcript_categories: list[str] = Field(default_factory=list)
    prefix_treatments: dict[Code, CreditTreatment] = Field(default_factory=dict)
    transcript_categories: dict[str, CreditTreatment] = Field(default_factory=dict)
    course_treatments: dict[Code, CreditTreatment] = Field(default_factory=dict)
    equivalent_courses: dict[Code, list[Code]] = Field(default_factory=dict)
    review_equivalences: dict[Code, list[Code]] = Field(default_factory=dict)
    transfer_credit_max: Amount | None = None
    credit_caps: list[CreditCap] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule(self):
        if self.admission_from > self.admission_to:
            raise ValueError("Invalid admission year range")
        requirements = (
            self.credit_requirements + self.course_requirements + self.nonacademic_requirements
            + self.verification_requirements
        )
        if len({item.id for item in requirements}) != len(requirements):
            raise ValueError("Duplicate requirement IDs")
        codes = [code for canonical, aliases in self.equivalent_courses.items()
                 for code in (canonical, *aliases)]
        if len(set(codes)) != len(codes):
            raise ValueError("Equivalent course groups must be disjoint")
        if self.major == "common" and (
            self.transcript_categories or self.course_treatments or self.equivalent_courses or self.credit_caps
            or self.department_major_codes or self.major_transcript_categories or self.prefix_treatments
        ):
            raise ValueError("Credit policies belong to the selected major rule")
        return self


class RuleFile(RuleModel):
    rules: list[GraduationRule] = Field(min_length=1)
