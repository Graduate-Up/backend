from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import account_db_path
from app.schemas.account import AccountResponse, Credentials, MeResponse, SessionResponse, StudentSettings
from app.schemas.rules import GraduationRule
from app.services.accounts import AccountStore, DuplicateUsername, get_account_store
from app.services.graduation_engine.rule_selector import RuleSource, select_rules

from .requirements import get_rule_provider

router = APIRouter(tags=["accounts"])
bearer = HTTPBearer(auto_error=False)


class ErrorResponse(BaseModel):
    detail: str


def account_store() -> AccountStore:
    return get_account_store(account_db_path())


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


Store = Annotated[AccountStore, Depends(account_store)]
Rules = Annotated[Callable[[], RuleSource], Depends(get_rule_provider)]


def session_token(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    if auth is None or len(auth.credentials) != 43 or not auth.credentials.isascii():
        raise unauthorized("로그인이 필요합니다.")
    return auth.credentials


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
def login(credentials: Credentials, store: Store) -> SessionResponse:
    session = store.login(credentials)
    if session is None:
        raise unauthorized("사용자 이름 또는 비밀번호가 올바르지 않습니다.")
    return session


@router.post("/auth/logout", status_code=204, responses=AUTH_ERROR)
def logout(token: Token, store: Store, _account: Account) -> Response:
    store.logout(token)
    return Response(status_code=204)


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
