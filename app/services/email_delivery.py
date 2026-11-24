import logging
import os
import re
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage

import httpx

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
EMAIL_SUBJECT = "졸UP 가입 인증번호"
logger = logging.getLogger("uvicorn.error")


class EmailUnavailable(RuntimeError):
    pass


def email_secret() -> bytes:
    secret = os.environ.get("JOLUP_EMAIL_CODE_SECRET", "").encode()
    if len(secret) < 32:
        raise EmailUnavailable("이메일 인증 서버 설정이 필요합니다.")
    return secret


def email_sender() -> Callable[[str, str], None]:
    transport = os.getenv("JOLUP_EMAIL_TRANSPORT", "smtp").strip().lower()
    if transport == "brevo":
        return brevo_sender()
    if transport == "smtp":
        return smtp_sender()
    raise EmailUnavailable("이메일 발송 방식 설정을 확인해 주세요.")


def email_text(code: str) -> str:
    return f"졸UP 인증번호는 {code}입니다. 10분 이내에 입력해 주세요."


def brevo_sender() -> Callable[[str, str], None]:
    key = os.getenv("JOLUP_BREVO_API_KEY", "").strip()
    sender = os.getenv("JOLUP_BREVO_FROM", "").strip()
    if not key or not sender:
        raise EmailUnavailable("Brevo API 키와 인증된 발송 주소 설정이 필요합니다.")

    def send(email: str, code: str) -> None:
        try:
            response = httpx.post(
                BREVO_SEND_URL,
                headers={"api-key": key, "Accept": "application/json"},
                json={
                    "sender": {"name": "졸UP", "email": sender},
                    "to": [{"email": email}],
                    "subject": EMAIL_SUBJECT,
                    "textContent": email_text(code),
                },
                timeout=10,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise EmailUnavailable("인증 메일 발송 서비스에 연결할 수 없습니다.") from exc
        if response.status_code == 201:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            message_id = payload.get("messageId") if isinstance(payload, dict) else None
            if isinstance(message_id, str) and re.fullmatch(r"[A-Za-z0-9<>@.+_=-]{1,256}", message_id):
                logger.info("Brevo accepted verification email message_id=%s", message_id)
            else:
                logger.info("Brevo accepted verification email without a usable message ID")
            return
        if response.status_code in {401, 403}:
            raise EmailUnavailable("Brevo가 발송 요청을 거절했습니다. API 키와 계정의 발송 권한을 확인해 주세요.")
        if response.status_code == 429:
            raise EmailUnavailable("메일 발송 서비스의 요청 한도에 도달했습니다. 잠시 뒤 다시 시도해 주세요.")
        if response.status_code in {400, 422}:
            raise EmailUnavailable("Brevo가 발송을 수락하지 않았습니다. 인증된 발송 주소 설정을 확인해 주세요.")
        raise EmailUnavailable("인증번호 발송에 실패했습니다. 잠시 뒤 다시 시도해 주세요.")

    return send


def smtp_sender() -> Callable[[str, str], None]:
    host = os.getenv("JOLUP_SMTP_HOST")
    sender = os.getenv("JOLUP_SMTP_FROM")
    user = os.getenv("JOLUP_SMTP_USERNAME")
    password = os.getenv("JOLUP_SMTP_PASSWORD")
    try:
        port = int(os.getenv("JOLUP_SMTP_PORT", "587"))
    except ValueError as exc:
        raise EmailUnavailable("이메일 발송 서버 설정을 확인해 주세요.") from exc
    if not host or not sender or not 1 <= port <= 65535 or bool(user) != bool(password):
        raise EmailUnavailable("이메일 발송 서버 설정이 필요합니다.")

    def send(email: str, code: str) -> None:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = email
        message["Subject"] = EMAIL_SUBJECT
        message.set_content(email_text(code))
        context = ssl.create_default_context()
        try:
            client = smtplib.SMTP_SSL(host, port, timeout=10, context=context) if port == 465 else (
                smtplib.SMTP(host, port, timeout=10)
            )
            with client:
                if port != 465:
                    client.starttls(context=context)
                if user and password:
                    client.login(user, password)
                client.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailUnavailable(
                "메일 서버가 발송 계정 인증을 거절했습니다. 발송용 이메일과 해당 계정의 앱 비밀번호를 확인해 주세요."
            ) from exc
        except smtplib.SMTPSenderRefused as exc:
            raise EmailUnavailable("메일 서버가 발송 주소를 거절했습니다. 발송 계정과 발송 주소 설정을 확인해 주세요.") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailUnavailable("메일 서버가 수신 주소를 거절했습니다. 학교 이메일 주소를 확인해 주세요.") from exc
        except ssl.SSLError as exc:
            raise EmailUnavailable("메일 서버의 TLS 인증에 실패했습니다. SMTP 주소와 포트 설정을 확인해 주세요.") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailUnavailable("인증번호 발송에 실패했습니다. 잠시 뒤 다시 시도해 주세요.") from exc

    return send
