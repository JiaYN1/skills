from typing import Literal

from pydantic import BaseModel, Field


ReviewCategory = Literal["性能", "设计", "安全", "可维护性", "错误处理", "测试", "规范", "逻辑"]
ReviewSeverity = Literal["严重", "建议", "规范"]


class PullRequestInfo(BaseModel):
    platform: str
    host: str
    repository: str
    number: str
    url: str
    title: str | None = None
    head_sha: str | None = None
    base_sha: str | None = None


class ReviewComment(BaseModel):
    id: str
    file_path: str
    line: int = Field(ge=1)
    side: Literal["RIGHT"] = "RIGHT"
    category: ReviewCategory
    severity: ReviewSeverity = "建议"
    message: str
    suggestion: str
    code_example: str
    language: str = ""
    body: str = ""
    publishable: bool = True
    publish_warning: str | None = None


class ReviewSummary(BaseModel):
    total: int
    severe: int
    suggestion: int
    style: int
    text: str


class ReviewRequest(BaseModel):
    pr_url: str = Field(min_length=8)
    scm_token: str | None = Field(default=None, description="GitHub/GitLab/GitCode token for private PRs or publishing.")
    model: str | None = Field(default=None, description="OpenAI-compatible chat model. Defaults to OPENAI_MODEL.")


class ReviewResponse(BaseModel):
    pr: PullRequestInfo
    comments: list[ReviewComment]
    summary: ReviewSummary
    warnings: list[str] = []


class PublishRequest(BaseModel):
    pr_url: str = Field(min_length=8)
    comments: list[ReviewComment]
    scm_token: str | None = Field(default=None, description="Token with permission to comment on the PR/MR.")


class PublishItemResult(BaseModel):
    id: str
    file_path: str
    line: int
    status: Literal["published", "skipped", "error"]
    url: str | None = None
    error: str | None = None


class PublishResponse(BaseModel):
    results: list[PublishItemResult]

