from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api import evaluation, majors, requirements
from app.services.graduation_engine.rule_loader import RuleDataError, RuleUnavailableError
from app.services.graduation_engine.rule_selector import ProfileError


def root():
    return {"message": "졸UP 백엔드 서버 실행 성공!"}


def requirements_page():
    return FileResponse(Path(__file__).with_name("requirements.html"), media_type="text/html")


def health_check():
    return {"status": "ok"}


async def profile_error(_request: Request, exc: ProfileError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def rule_data_error(_request: Request, exc: RuleDataError):
    code = "rules_unavailable" if isinstance(exc, RuleUnavailableError) else "invalid_rule_data"
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": code})


def create_app() -> FastAPI:
    app = FastAPI(
        title="졸UP Backend API",
        description="경북대학교 컴퓨터학부 졸업요건 도우미",
        version="0.1.0",
    )
    app.get("/")(root)
    app.get("/health")(health_check)
    app.get("/requirements", include_in_schema=False)(requirements_page)
    for router in (majors.router, requirements.router, evaluation.router):
        app.include_router(router, prefix="/api/v1")
    app.add_exception_handler(ProfileError, profile_error)
    app.add_exception_handler(RuleDataError, rule_data_error)
    return app


app = create_app()
