from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from .student import StudentProfile

Username = Annotated[str, StringConstraints(
    strip_whitespace=True, to_lower=True, min_length=3, max_length=64,
    pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
)]
StudentNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=r"^[0-9]+$")]
SchoolEmail = Annotated[str, StringConstraints(
    strip_whitespace=True, to_lower=True, max_length=75,
    pattern=r"^[A-Za-z0-9][A-Za-z0-9_+.-]{0,63}@([kK][nN][uU]\.[aA][cC]\.[kK][rR])$",
)]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: SchoolEmail
    password: SecretStr = Field(min_length=8, max_length=128)


class EmailCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: SchoolEmail
    purpose: Literal["register", "reset"] = Field(
        "register", description="register: 회원가입 인증번호, reset: 비밀번호 재설정 인증번호",
    )


class Registration(Credentials):
    code: SecretStr = Field(min_length=6, max_length=6)
    legacy_username: Username | None = None


class PasswordReset(Credentials):
    """password는 새 비밀번호."""
    code: SecretStr = Field(min_length=6, max_length=6)


class StudentSettings(StudentProfile):
    student_number: StudentNumber
    convergence_program: str | None = Field(
        default=None, min_length=1, description="ID from GET /api/v1/convergence-programs. Send null to clear.",
    )


class AccountResponse(BaseModel):
    id: int
    username: SchoolEmail


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    account: AccountResponse
    profile: StudentSettings | None
