from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from .student import StudentProfile

Username = Annotated[str, StringConstraints(
    strip_whitespace=True, to_lower=True, min_length=3, max_length=64,
    pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
)]
StudentNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=r"^[0-9]+$")]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username
    password: SecretStr = Field(min_length=8, max_length=128)


class StudentSettings(StudentProfile):
    student_number: StudentNumber


class AccountResponse(BaseModel):
    id: int
    username: Username


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    account: AccountResponse
    profile: StudentSettings | None
