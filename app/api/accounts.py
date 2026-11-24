import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import account_db_path
from app.schemas.account import (
    AccountResponse, Credentials, EmailCodeRequest, MeResponse, PasswordReset, Registration, SessionResponse,
    StudentSettings,
)
from app.schemas.course import Course
from app.schemas.evaluation import ChecklistUpdate, EvaluationResponse, ProgressResponse, StudentRecords
from app.schemas.rules import GraduationRule
from app.services.accounts import (
    AccountStore, DuplicateUsername, EmailCodeRateLimit, InvalidEmailCode, InvalidLegacyAccount, get_account_store,
)
from app.services.course_metadata import CareerTag, computer_science_offering
from app.services.demo_account import is_demo_email
from app.services.email_delivery import EmailUnavailable, email_secret, email_sender
from app.services.graduation_chat import ChatUnavailable, GraduationAnswer, GraduationQuestion, answer_graduation_question
from app.services.graduation_engine.evaluator import evaluate_graduation, evaluate_selected_rule
from app.services.graduation_engine.progress import build_progress, toggle_checklist
from app.services.graduation_engine.rule_loader import RuleDataError
from app.services.graduation_engine.rule_selector import RuleSource, select_rules
from app.services.recommendations import Recommendations, programs_catalog, recommend
from app.services.transcript import TranscriptImportInput, TranscriptInput, TranscriptPreview, parse_transcript

from .requirements import get_rule_provider, graduation_schedule

logger = logging.getLogger(__name__)
router = APIRouter(tags=["accounts"])
bearer = HTTPBearer(auto_error=False)
SESSION_COOKIE = "jolup_session"
browser_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


class ErrorResponse(BaseModel):
    detail: str


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    career: CareerTag | None = None
    use_ai: bool = False
    target_semester: str | None = Field(default=None, pattern=r"^[0-9]{4}-[12]$")


class TranscriptImportResponse(BaseModel):
    added: int
    scanned: int
    excluded_failed: int
    needs_review: int
    grades_filled: int = 0
    liberal_summary_saved: bool = False


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


@router.post("/auth/email-code", status_code=204, responses={
    429: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
})
def send_email_code(
    payload: EmailCodeRequest, store: Store, request: Request, background: BackgroundTasks,
) -> Response:
    """인증번호를 보낸다. purpose=reset이면 가입된 이메일일 때만 실제로 보내지만, 가입 여부와 관계없이
    같은 응답(204)을 돌려준다. 재설정 메일은 응답 뒤에 보내서 응답 시간으로도 가입 여부가 드러나지 않는다."""
    try:
        send = email_sender()
        issuer = request.client.host if request.client else ""
        code = store.issue_email_code(payload.email, issuer, email_secret(), payload.purpose)
        if payload.purpose == "reset":
            # 데모 계정은 여럿이 같이 쓰므로 비밀번호를 바꿀 수 없게 한다 (응답은 똑같이 204)
            if code is not None and not is_demo_email(payload.email):
                background.add_task(send_quietly, send, payload.email, code)
        else:
            send(payload.email, code)
    except EmailCodeRateLimit as exc:
        raise HTTPException(429, "인증번호 요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요.") from exc
    except EmailUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(status_code=204)


def send_quietly(send: Callable[[str, str], None], email: str, code: str) -> None:
    try:
        send(email, code)
    except EmailUnavailable as exc:
        logger.warning("비밀번호 재설정 인증번호 발송 실패: %s", exc)


@router.post("/auth/register", status_code=201, responses={
    400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
})
def register(credentials: Registration, store: Store) -> AccountResponse:
    try:
        return store.register(credentials, email_secret())
    except InvalidEmailCode as exc:
        raise HTTPException(400, "인증번호가 틀렸거나 만료됐습니다. 필요한 경우 다시 발급받아 주세요.") from exc
    except InvalidLegacyAccount as exc:
        raise HTTPException(400, "기존 사용자 이름과 비밀번호를 확인해 주세요.") from exc
    except DuplicateUsername as exc:
        raise HTTPException(409, str(exc)) from exc
    except EmailUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/auth/password-reset", responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def reset_password(reset: PasswordReset, store: Store, request: Request, response: Response) -> SessionResponse:
    """재설정 인증번호(email-code purpose=reset)가 맞으면 비밀번호를 바꾸고 기존 세션을 모두 끊은 뒤 바로 로그인한다."""
    try:
        session = store.reset_password(reset, email_secret())
    except InvalidEmailCode as exc:
        raise HTTPException(400, "인증번호가 틀렸거나 만료됐습니다. 필요한 경우 다시 발급받아 주세요.") from exc
    except EmailUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return start_session(session, request, response)


@router.post("/auth/login", responses=AUTH_ERROR)
def login(credentials: Credentials, store: Store, request: Request, response: Response) -> SessionResponse:
    session = store.login(credentials)
    if session is None:
        raise unauthorized("학교 이메일 또는 비밀번호가 올바르지 않습니다.")
    return start_session(session, request, response)


def start_session(session: SessionResponse, request: Request, response: Response) -> SessionResponse:
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
    if profile.convergence_program is not None and not any(
        program.kind == "convergence" and program.id == profile.convergence_program
        for program in programs_catalog()
    ):
        raise HTTPException(422, "등록된 융합전공 ID를 선택해 주세요.")
    saved = store.save_profile(account.account.id, profile)
    return MeResponse(account=account.account, profile=saved)


@router.get("/me/requirements", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}})
def my_requirements(account: Account, provide_rules: Rules) -> GraduationRule:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    return select_rules(account.profile, provide_rules())


def records_with_course_offerings(records: StudentRecords) -> StudentRecords:
    courses = [
        course.model_copy(update={"offered_by_computer_science": computer_science_offering(
            course.course_code, course.offered_by_computer_science, prior_institution=course.prior_institution,
        )})
        for course in records.completed_courses
    ]
    return records.model_copy(update={"completed_courses": courses})


@router.get("/me/records", responses=AUTH_ERROR)
def my_records(account: Account, store: Store) -> StudentRecords:
    return records_with_course_offerings(store.get_records(account.account.id))


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
    ready = [item for item in preview.courses if item.completed]
    candidates = [(item, "completed") for item in ready] + [(item, "failed") for item in preview.failed_courses]
    courses = [
        Course(**item.model_dump(exclude={"completed", "semester_label"}), status=status)
        for item, status in candidates if item.semester and item.transcript_category and item.credits is not None
    ]
    try:
        added, grades_filled = store.append_courses(account.account.id, courses, payload.liberal_summary)
    except ValueError as exc:
        raise HTTPException(409, "저장할 수 있는 이수 내역 수를 초과했습니다.") from exc
    return TranscriptImportResponse(
        added=added, scanned=count, excluded_failed=preview.excluded_failed, needs_review=preview.needs_review,
        grades_filled=grades_filled, liberal_summary_saved=payload.liberal_summary is not None,
    )


@router.put("/me/records", responses=AUTH_ERROR, description="과목과 비교과 기록 전체를 교체합니다.")
def save_records(records: StudentRecords, account: Account, store: Store) -> StudentRecords:
    return records_with_course_offerings(store.save_records(account.account.id, records))


@router.get("/me/evaluation", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}})
def my_evaluation(account: Account, store: Store, provide_rules: Rules) -> EvaluationResponse:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    records = store.get_records(account.account.id)
    return evaluate_graduation(
        account.profile, records.completed_courses, records.nonacademic_records, provide_rules(), records.liberal_summary,
    )


@router.post("/me/graduation-chat", responses={
    **AUTH_ERROR, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
})
def my_graduation_chat(
    request: GraduationQuestion, account: Account, store: Store, provide_rules: Rules,
) -> GraduationAnswer:
    profile = account.profile
    if profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    rule = select_rules(profile, provide_rules())
    records = store.get_records(account.account.id)
    evaluation = evaluate_selected_rule(
        profile, records.completed_courses, records.nonacademic_records, rule, records.liberal_summary,
    )
    try:
        schedule = graduation_schedule(profile.expected_graduation_date, profile.major, profile.admission_year)
    except RuleDataError:
        logger.warning("Graduation schedule unavailable for chat")
        schedule = None
    schedule_data = schedule.model_dump(mode="json") if schedule is not None else None
    if schedule_data is not None:
        classification = schedule_data["classification_change"]
        classification["sources"].extend(classification.pop("major_sources").get(profile.major, []))
        classification["major_department_code"] = classification.pop("major_department_codes").get(profile.major)
    try:
        return answer_graduation_question(
            request.question, evaluation, rule, schedule_data, profile, records,
        )
    except ChatUnavailable as exc:
        raise HTTPException(503, "지금은 졸업요건 AI 답변을 받을 수 없어요.") from exc


@router.get(
    "/me/progress", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}},
    description="대시보드 진행률. 이수 과목으로 판정되는 요건(course)만 서버가 계산하고, "
                "비교과 요건(checklist)은 저장된 체크 상태와 함께 내려줍니다. "
                "프런트는 체크 변경 시 items로 진행률을 즉시 다시 계산합니다.",
)
def my_progress(account: Account, store: Store, provide_rules: Rules) -> ProgressResponse:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    records = store.get_records(account.account.id)
    return build_progress(
        account.profile, records.completed_courses, records.nonacademic_records, provide_rules(), records.liberal_summary,
    )


@router.post("/me/recommendations", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}})
def my_recommendations(
    request: RecommendationRequest, account: Account, store: Store, provide_rules: Rules,
) -> Recommendations:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    rule = select_rules(account.profile, provide_rules())
    try:
        return recommend(account.profile, store.get_records(account.account.id), rule,
                         request.career, request.use_ai, request.target_semester)
    except (OSError, ValueError, KeyError) as exc:
        raise RuleDataError("추천 교육과정 데이터를 읽을 수 없습니다.") from exc


@router.put(
    "/me/checklist", responses={**AUTH_ERROR, 409: {"model": ErrorResponse}},
    description="체크리스트(비교과) 항목 하나의 체크 상태를 저장하고 갱신된 진행률을 반환합니다.",
)
def save_checklist(update: ChecklistUpdate, account: Account, store: Store, provide_rules: Rules) -> ProgressResponse:
    if account.profile is None:
        raise HTTPException(409, "학생 설정을 먼저 저장해 주세요.")
    rules = provide_rules()
    rule = select_rules(account.profile, rules)
    try:
        records = store.update_nonacademic(
            account.account.id,
            lambda current: toggle_checklist(
                rule, current, update.requirement_id, update.checked, update.amount, update.english_test,
            ),
        )
    except KeyError as exc:
        raise HTTPException(422, "현재 졸업요건에 없는 체크리스트 항목입니다.") from exc
    return build_progress(
        account.profile, records.completed_courses, records.nonacademic_records, rules, records.liberal_summary,
    )
