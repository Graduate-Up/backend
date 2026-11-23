import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.course import Amount, Code
from app.schemas.evaluation import Source
from app.schemas.rules import (
    Conditions, CourseOption, CourseRequirement, CreditCap, CreditRequirement,
    CreditTreatment, GraduationRule, NonacademicRequirement, VerificationRequirement,
)


class Reference(BaseModel):
    id: str
    title: str
    url: str | None
    date: str


class Cited(BaseModel):
    source: list[str] = Field(min_length=1)


class Minimum(Cited):
    min: Amount


class MajorMinimum(Cited):
    by_multi_major: dict[str, Amount]


class LiberalMinimum(Minimum):
    sdg_min: Amount
    areas: list["LiberalArea"]


class LiberalArea(BaseModel):
    id: str
    any_of: list[str] = Field(min_length=1)
    min_credits: Amount


class Prefix(Cited):
    prefix: Code


class FutureDesign(Prefix):
    max_counted: Amount
    applies_from_admission_year: int


class Credits(BaseModel):
    total: Minimum
    major: MajorMinimum
    liberal_arts: LiberalMinimum
    future_design: FutureDesign
    startup_courses: Prefix


class RequiredCourse(BaseModel):
    code: Code
    name: str
    equivalent_codes: list[Code]
    co_requisite: Code | None = None


class RequiredCourses(Cited):
    all_of: list[RequiredCourse] = Field(min_length=1)
    must_be_classified_as: str


class FieldPractice(Cited):
    min_credits: Amount


class Counseling(Cited):
    min_sessions: Amount


class Qualification(BaseModel):
    practical_english: Cited
    software: Cited
    counseling: Counseling


class Gpa(Cited):
    value: Amount


class Confirmation(BaseModel):
    id: str
    question: str
    sources: list[str] = Field(min_length=1)


class AicomDocument(BaseModel):
    id: str
    schema_version: Literal["1.0"]
    major: dict[str, str]
    admission_years: tuple[Literal[2024], Literal[2024]]
    sources: list[Reference] = Field(min_length=1)
    credits: Credits
    required_courses: RequiredCourses
    field_practice: FieldPractice
    graduation_qualification: Qualification
    min_gpa: Gpa
    needs_confirmation: list[Confirmation]


def import_aicom_rule(path: Path) -> GraduationRule:
    document = AicomDocument.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
    if document.major.get("code") != "aicom":
        raise ValueError("Expected the 2024 AICOM rule document")
    sources = {item.id: Source(title=item.title, url=item.url, reference=item.date) for item in document.sources}
    if len(sources) != len(document.sources):
        raise ValueError("Duplicate AICOM source IDs")

    def cite(section: Cited) -> list[Source]:
        return [sources[key] for key in section.source]

    credits = document.credits
    required = document.required_courses
    qualification = document.graduation_qualification
    options = [CourseOption(course_code=item.code, course_name=item.name) for item in required.all_of]
    verification = [
        VerificationRequirement(id=item.id, name=item.question, sources=[sources[key] for key in item.sources])
        for item in document.needs_confirmation
        if item.id not in {"NC-TRANSFER-MINOR1", "NC-AICOM-ENG-EXEMPT", "NC-RETAKE-GRADE"}
    ]
    verification.extend(VerificationRequirement(
        id=f"corequisite-{item.code}", name=f"{item.name} 관련 과목 {item.co_requisite} 이수 기준",
        sources=cite(required),
    ) for item in required.all_of if item.co_requisite)
    return GraduationRule(
        id=document.id, dataset="official", major="aicom", admission_from=2024, admission_to=2024,
        conditions=Conditions(
            multi_majors=["none"], transfer_student=False, international_student=False, disabled_student=False, abeek_values=[None, False],
        ),
        sources=list(sources.values()),
        credit_requirements=[
            CreditRequirement(id="total", name="총 졸업학점", area="total", minimum=credits.total.min,
                              sources=cite(credits.total)),
            CreditRequirement(id="major", name="전공학점", area="major",
                              minimum=credits.major.by_multi_major["none"], sources=cite(credits.major)),
            CreditRequirement(id="liberal", name="교양학점", area="liberal_arts",
                              minimum=credits.liberal_arts.min, sources=cite(credits.liberal_arts)),
        ],
        course_requirements=[CourseRequirement(
            id="required-major", name="전공필수 교과목", courses=options, choose=len(options),
            required_transcript_category=required.must_be_classified_as, require_department=True,
            sources=cite(required),
        )],
        nonacademic_requirements=[
            NonacademicRequirement(id=key, name=name, minimum=minimum, sources=cite(section))
            for key, name, minimum, section in (
                ("field-practice", "현장실습 인정학점 또는 승인된 대체 실적", document.field_practice.min_credits,
                 document.field_practice),
                ("practical-english", "실용영어 졸업자격인정", 1, qualification.practical_english),
                ("software", "소프트웨어 졸업자격인정", 1, qualification.software),
                ("counseling", "지도교수 상담 횟수", qualification.counseling.min_sessions, qualification.counseling),
                ("gpa", "평점평균", document.min_gpa.value, document.min_gpa),
                ("sdg", "SDG 교양 인정학점", credits.liberal_arts.sdg_min, credits.liberal_arts),
                *((area.id, "교양 영역: " + " / ".join(area.any_of), area.min_credits, credits.liberal_arts)
                  for area in credits.liberal_arts.areas),
            )
        ],
        verification_requirements=verification,
        transcript_categories={
            key: CreditTreatment(category=category)
            for key, category in {
                "교양": "liberal_arts", "교양필수": "liberal_arts", "교양선택": "liberal_arts",
                "일반선택": "general", "다중전공": "general", "현장실습": "general",
            }.items()
        },
        department_major_codes=[item.code for item in required.all_of],
        major_transcript_categories=[required.must_be_classified_as, "전공선택"],
        prefix_treatments={
            section.prefix: CreditTreatment(category="general")
            for section in (credits.future_design, credits.startup_courses)
        },
        equivalent_courses={item.code: item.equivalent_codes for item in required.all_of if item.equivalent_codes},
        credit_caps=[CreditCap(maximum=credits.future_design.max_counted, code_prefix=credits.future_design.prefix)]
        if 2024 >= credits.future_design.applies_from_admission_year else [],
    )
