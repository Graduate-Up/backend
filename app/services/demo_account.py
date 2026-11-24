"""시연용 데모 계정.

배포 서버(Render 무료 플랜)는 배포할 때마다 DB가 초기화되므로, 서버가 시작될 때 데모 계정을 만들어 둔다.
- JOLUP_DEMO_EMAIL: 학교 이메일 형식 (예: jolup-demo@knu.ac.kr)
- JOLUP_DEMO_PASSWORD: 8자 이상
둘 다 있을 때만 만든다. 학적·이수 내역은 data/demo/demo_student.json (가상 학생)을 넣는다.
이미 있는 계정은 건드리지 않고, 데모 이메일로는 비밀번호 재설정 메일을 보내지 않는다.
"""

import json
import logging
import os

from pydantic import ValidationError

from app.core.config import PROJECT_DIRECTORY, account_db_path
from app.schemas.account import Credentials, StudentSettings
from app.schemas.evaluation import StudentRecords
from app.services.accounts import get_account_store

logger = logging.getLogger(__name__)
DEMO_DATA = PROJECT_DIRECTORY / "data" / "demo" / "demo_student.json"


def demo_credentials() -> Credentials | None:
    email = os.getenv("JOLUP_DEMO_EMAIL", "").strip()
    password = os.getenv("JOLUP_DEMO_PASSWORD", "")
    if not email or not password:
        return None
    try:
        return Credentials(email=email, password=password)
    except ValidationError:
        logger.warning("데모 계정을 만들지 않음: JOLUP_DEMO_EMAIL은 @knu.ac.kr 이메일, JOLUP_DEMO_PASSWORD는 8자 이상이어야 함")
        return None


def is_demo_email(email: str) -> bool:
    credentials = demo_credentials()
    return credentials is not None and credentials.email == email


def seed_demo_account() -> None:
    credentials = demo_credentials()
    if credentials is None:
        return
    data = json.loads(DEMO_DATA.read_text(encoding="utf-8"))
    created = get_account_store(account_db_path()).create_verified_account(
        credentials, StudentSettings.model_validate(data["profile"]), StudentRecords.model_validate(data["records"]),
    )
    logger.info("데모 계정 %s", "생성함" if created else "이미 있음 (그대로 둠)")
