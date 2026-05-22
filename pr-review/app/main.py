from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .providers import ProviderError, PullRequestData, fetch_pull_request
from .publisher import PublishError, publish_comments
from .reviewer import ReviewError, generate_review
from .schemas import LoginRequest, PublishRequest, PublishResponse, PullRequestInfo, ReviewRequest, ReviewResponse


SESSION_COOKIE_NAME = "pr_review_session"
static_dir = Path(__file__).resolve().parent.parent / "static"


app = FastAPI(title="PR Review Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def password_gate(request: Request, call_next):
    password = _access_password()
    if not password or _is_public_path(request.url.path) or _has_valid_session(request, password):
        return await call_next(request)

    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": "未授权，请先输入访问密码。"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if request.method in {"GET", "HEAD"}:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    return JSONResponse(
        {"detail": "未授权，请先输入访问密码。"},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", include_in_schema=False)
async def login_page() -> Response:
    if not _access_password():
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(static_dir / "login.html")


@app.post("/api/login", include_in_schema=False)
async def login(request: LoginRequest, response: Response) -> dict[str, str]:
    password = _access_password()
    if not password:
        return {"status": "disabled"}
    if not hmac.compare_digest(request.password, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="密码错误。")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_session(password),
        max_age=_session_ttl_seconds(),
        httponly=True,
        samesite="lax",
    )
    return {"status": "ok"}


@app.post("/api/logout", include_in_schema=False)
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}


@app.post("/api/review", response_model=ReviewResponse)
async def review_pr(request: ReviewRequest) -> ReviewResponse:
    try:
        data = await fetch_pull_request(request.pr_url, token=request.scm_token)
        comments, summary, warnings = await generate_review(data, model=request.model)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ReviewResponse(pr=_pull_request_info(data), comments=comments, summary=summary, warnings=warnings)


@app.post("/api/publish", response_model=PublishResponse)
async def publish_review_comments(request: PublishRequest) -> PublishResponse:
    try:
        results = await publish_comments(request.pr_url, request.comments, token=request.scm_token)
    except (ProviderError, PublishError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PublishResponse(results=results)


def _pull_request_info(data: PullRequestData) -> PullRequestInfo:
    return PullRequestInfo(
        platform=data.ref.platform,
        host=data.ref.host,
        repository=data.ref.project_path,
        number=data.ref.number,
        url=data.ref.web_url,
        title=data.title,
        head_sha=data.head_sha,
        base_sha=data.base_sha,
    )


app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


def _access_password() -> str:
    return os.getenv("ACCESS_PASSWORD", "").strip()


def _session_ttl_seconds() -> int:
    value = os.getenv("ACCESS_SESSION_TTL_SECONDS", "43200").strip()
    try:
        return max(int(value), 60)
    except ValueError:
        return 43200


def _is_public_path(path: str) -> bool:
    return path in {"/api/health", "/api/login", "/login", "/favicon.ico"}


def _has_valid_session(request: Request, password: str) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return False

    try:
        state, issued_at, signature = cookie.split(":", 2)
    except ValueError:
        return False

    if state != "ok" or not issued_at.isdigit():
        return False

    payload = f"{state}:{issued_at}"
    expected = _sign_value(payload, password)
    if not hmac.compare_digest(signature, expected):
        return False

    return (int(time.time()) - int(issued_at)) <= _session_ttl_seconds()


def _sign_session(password: str) -> str:
    issued_at = str(int(time.time()))
    payload = f"ok:{issued_at}"
    signature = _sign_value(payload, password)
    return f"{payload}:{signature}"


def _sign_value(payload: str, password: str) -> str:
    secret = os.getenv("ACCESS_SESSION_SECRET", "").strip()
    if not secret:
        secret = hashlib.sha256(f"pr-review:{password}".encode("utf-8")).hexdigest()
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
