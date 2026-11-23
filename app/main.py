import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api import accounts, courses, evaluation, majors, requirements
from app.services.graduation_engine.rule_loader import RuleDataError, RuleUnavailableError
from app.services.graduation_engine.rule_selector import ProfileError


def root():
    return {"message": "졸UP 백엔드 서버 실행 성공!"}


def requirements_page():
    return FileResponse(Path(__file__).with_name("requirements.html"), media_type="text/html")


def requirements_view_script():
    return FileResponse(Path(__file__).with_name("requirements-view.js"), media_type="text/javascript")


def dashboard_view_script():
    return FileResponse(Path(__file__).with_name("dashboard-view.js"), media_type="text/javascript")


def transcript_bookmarklet_script():
    return FileResponse(Path(__file__).with_name("transcript-bookmarklet.js"), media_type="text/plain")


def account_page():
    return FileResponse(Path(__file__).with_name("account.html"), media_type="text/html")


def health_check():
    return {"status": "ok"}


async def profile_error(_request: Request, exc: ProfileError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def rule_data_error(_request: Request, exc: RuleDataError):
    code = "rules_unavailable" if isinstance(exc, RuleUnavailableError) else "invalid_rule_data"
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": code})


def is_account_request(request: Request) -> bool:
    path = request.url.path
    return path.startswith("/api/v1/auth/") or path == "/api/v1/me" or path.startswith("/api/v1/me/")


async def validation_error(request: Request, exc: RequestValidationError):
    if is_account_request(request):
        errors = [{key: item[key] for key in ("loc", "msg", "type")} for item in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)


async def private_account_responses(request: Request, call_next):
    response = await call_next(request)
    if is_account_request(request):
        response.headers["Cache-Control"] = "no-store"
    return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="졸UP Backend API",
        description="경북대학교 컴퓨터학부 졸업요건 도우미",
        version="0.1.0",
    )
    app.get("/")(root)
    app.get("/health")(health_check)
    app.get("/requirements", include_in_schema=False)(requirements_page)
    app.get("/requirements-view.js", include_in_schema=False)(requirements_view_script)
    app.get("/dashboard-view.js", include_in_schema=False)(dashboard_view_script)
    app.get("/transcript-bookmarklet.js", include_in_schema=False)(transcript_bookmarklet_script)
    app.get("/account", include_in_schema=False)(account_page)
    for router in (majors.router, requirements.router, evaluation.router, accounts.router, courses.router):
        app.include_router(router, prefix="/api/v1")
    app.add_exception_handler(ProfileError, profile_error)
    app.add_exception_handler(RuleDataError, rule_data_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.middleware("http")(private_account_responses)
    origins = [origin.strip() for origin in os.getenv("JOLUP_FRONTEND_ORIGINS", "").split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("Configure explicit origins in JOLUP_FRONTEND_ORIGINS")
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type"],
        )
    return app


app = create_app()
