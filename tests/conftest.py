import pytest

from app.api import accounts
from app.schemas.course import Course
from app.schemas.student import StudentProfile
from tests.demo_app import demo_rules


@pytest.fixture(autouse=True)
def verification_mailbox(monkeypatch):
    mailbox = {}
    monkeypatch.setenv("JOLUP_EMAIL_CODE_SECRET", "a" * 32)
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "JOLUP_AI_PROVIDER", "JOLUP_GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(accounts, "email_sender", lambda: mailbox.__setitem__)
    return mailbox


@pytest.fixture
def rules():
    return demo_rules()


@pytest.fixture
def student():
    return StudentProfile(admission_year=2024, major="aicom")


@pytest.fixture
def course():
    def make(code="TEST001", credits=3, category="major", status="completed", semester="2024-1"):
        return Course(
            course_code=code, credits=credits, transcript_category=category, status=status, semester=semester,
        )

    return make
