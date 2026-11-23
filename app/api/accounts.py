from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import account_db_path
from app.schemas.account import AccountResponse, Credentials, MeResponse, SessionResponse, StudentSettings
from app.schemas.course import Course
from app.schemas.evaluation import EvaluationResponse, StudentRecords
from app.schemas.rules import GraduationRule
from app.services.accounts import AccountStore, DuplicateUsername, get_account_store
from app.services.graduation_engine.evaluator import evaluate_graduation
from app.services.graduation_engine.rule_selector import RuleSource, select_rules
from app.services.transcript import TranscriptImportInput, TranscriptInput, TranscriptPreview, parse_transcript

from .requirements import get_rule_provider

router = APIRouter(tags=["accounts"])
bearer = HTTPBearer(auto_error=False)
SESSION_COOKIE = "jolup_session"
browser_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


class ErrorResponse(BaseModel):
    detail: str


class TranscriptImportResponse(BaseModel):
    added: int
    scanned: int
    excluded_failed: int
    needs_review: int


def account_store() -> AccountStore:
    return get_account_store(account_db_path())


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


Store = Annotated[AccountStore, Depends(account_store)]
Rules = Annotated[Callable[[], RuleSource], Depends(get_rule_provider)]


def session_token(
    request: Request,
    auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    cookie: Annotated[str | None, Depends(browser_cookie)],
) -> str:
    token = auth.credentials if auth else cookie
    if token is None or len(token) != 43 or not token.isascii():
        raise unauthorized("로그인이 필요합니다.")
    if auth is None and request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_same_origin(request)
    return token


def require_same_origin(request: Request) -> None:
    if request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "다른 출처에서는 브라우저 세션으로 저장할 수 없습니다.")


Token = Annotated[str, Depends(session_token)]


def current_account(token: Token, store: Store) -> MeResponse:
    account = store.get_me(token)
    if account is None:
        raise unauthorized("세션이 만료되었거나 유효하지 않습니다.")
    return account


Account = Annotated[MeResponse, Depends(current_account)]
AUTH_ERROR = {401: {"model": ErrorResponse, "description": "유효한 로그인 정보 또는 세션 필요"}}


@router.post("/auth/register", status_code=201, responses={409: {"model": ErrorResponse}})
def register(credentials: Credentials, store: Store) -> AccountResponse:
    try:
        return store.register(credentials)
    except DuplicateUsername as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/auth/login", responses=AUTH_ERROR)
def login(credentials: Credentials, store: Store, request: Request, response: Response) -> SessionResponse:
    session = store.login(credentials)
    if session is None:
        raise unauthorized("사용자 이름 또는 비밀번호가 올바르지 않습니다.")
    origin = request.headers.get("origin")
    if origin is None or origin == f"{request.url.scheme}://{request.url.netloc}":
        response.set_cookie(
            SESSION_COOKIE, session.access_token, max_age=session.expires_in, path="/api/v1",
            httponly=True, samesite="lax", secure=request.url.scheme == "https",
        )
    return session


@router.post("/auth/logout", status_code=204, responses=AUTH_ERROR)
def logout(token: Token, store: Store, _account: Account) -> Response:
    store.logout(token)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/api/v1")
    return response


@router.get("/me", responses=AUTH_ERROR)
def me(account: Account) -> MeResponse:
    return account


@router.put("/me/profile", responses=AUTH_ERROR)
def save_profile(profile: StudentSettings, account: Account, store: Store, provide_rules: Rules) -> MeResponse:
    select_rules(profile, provide_rules())
    store.save_profile(account.account.id, profile)
    return MeResponse(account=account.account, profile=profile)


@router.get("/me/requirements", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}})
def my_requirements(account: Account, provide_rules: Rules) -> GraduationRule:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    return select_rules(account.profile, provide_rules())


@router.get("/me/records", responses=AUTH_ERROR)
def my_records(account: Account, store: Store) -> StudentRecords:
    return store.get_records(account.account.id)


@router.post("/me/transcript/parse", responses=AUTH_ERROR, description="복사한 성적표를 파싱합니다. 원문을 저장하지 않습니다.")
def preview_transcript(payload: TranscriptInput, _account: Account) -> TranscriptPreview:
    try:
        return parse_transcript(payload.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/me/transcript/import", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}})
def import_transcript(payload: TranscriptImportInput, account: Account, store: Store) -> TranscriptImportResponse:
    preview = preview_transcript(payload, account)
    count = len(preview.courses) + preview.excluded_failed + preview.duplicates_removed
    if count != payload.expected:
        raise HTTPException(409, "전체 행이나 이수 정보를 확인하지 못해 자동 저장하지 않았습니다.")
    courses = [
        Course(**item.model_dump(exclude={"completed", "semester_label"}), status="completed")
        for item in preview.courses if item.completed and item.semester and item.transcript_category and item.credits is not None
    ]
    try:
        added = store.append_courses(account.account.id, courses)
    except ValueError as exc:
        raise HTTPException(409, "저장할 수 있는 이수 내역 수를 초과했습니다.") from exc
    return TranscriptImportResponse(
        added=added, scanned=count, excluded_failed=preview.excluded_failed, needs_review=preview.needs_review,
    )


@router.put("/me/records", responses=AUTH_ERROR, description="과목과 비교과 기록 전체를 교체합니다.")
def save_records(records: StudentRecords, account: Account, store: Store) -> StudentRecords:
    store.save_records(account.account.id, records)
    return records


@router.get("/me/evaluation", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}})
def my_evaluation(account: Account, store: Store, provide_rules: Rules) -> EvaluationResponse:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    records = store.get_records(account.account.id)
    return evaluate_graduation(account.profile, records.completed_courses, records.nonacademic_records, provide_rules())
