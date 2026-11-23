from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(str.upper)]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Category = Literal["major", "liberal_arts", "general"]


class Course(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: Code
    semester: str = Field(min_length=1)
    credits: Amount
    transcript_category: str = Field(min_length=1)
    offered_by_computer_science: bool | None = None
    prior_institution: bool | None = None
    status: Literal["completed", "in_progress", "failed", "incomplete"]
