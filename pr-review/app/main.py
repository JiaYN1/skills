from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .providers import ProviderError, PullRequestData, fetch_pull_request
from .publisher import PublishError, publish_comments
from .reviewer import ReviewError, generate_review
from .schemas import PublishRequest, PublishResponse, PullRequestInfo, ReviewRequest, ReviewResponse


app = FastAPI(title="PR Review Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
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


static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

