from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Major = Literal["glsop", "plsop", "aicom", "simcom", "chumcom"]
MultiMajor = Literal[
    "none", "double_major", "minor", "interdisciplinary",
    "double_or_linked_or_convergence", "minor_1_only", "minor_2plus", "teaching_course",
]
StudentStatus = Literal["new", "general_transfer", "bachelor_transfer", "foreign", "disabled"]


class StudentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admission_year: int = Field(ge=1900, le=2200)
    major: Major
    track: str | None = Field(default=None, min_length=1)
    track_variant: str | None = Field(default=None, min_length=1)
    abeek: bool | None = None
    multi_major: MultiMajor = "none"
    expected_graduation_date: str | None = Field(
        default=None, pattern=r"^(?:19[0-9]{2}|20[0-9]{2}|21[0-9]{2}|2200)-(?:02|08)$",
        description="졸업 예정 연·월 (YYYY-02 또는 YYYY-08)",
        examples=["2027-02", "2027-08"],
    )
    transfer_student: bool = False
    student_status: StudentStatus | None = None
    international_student: bool = False
    disabled_student: bool = False
