# PR Review Service

把 `pr-review` skill 封装成可部署服务：前端输入 PR/MR 链接，后端拉取 diff，调用 OpenAI-compatible Chat Completions 生成结构化 review 意见，并可将选中的意见发布到识别到的 PR 代码行。

## 支持范围

- GitHub: `https://github.com/{owner}/{repo}/pull/{number}`
- GitLab/self-hosted GitLab: `https://{host}/{group}/{repo}/-/merge_requests/{iid}`
- GitCode: `https://gitcode.com/{owner}/{repo}/pull/{number}`，使用 GitCode v5 API

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export OPENAI_API_KEY=sk-...
uvicorn app.main:app --host 0.0.0.0 --port 19986
```

打开 `http://localhost:19986`。

## Docker

```bash
docker build -t pr-review-service .
docker run --rm -p 19986:19986 \
  -e OPENAI_API_KEY=sk-... \
  -e GITHUB_TOKEN=github_pat_... \
  pr-review-service
```

## 环境变量

- `OPENAI_API_KEY`: 必填，用于生成 review。
- `OPENAI_MODEL`: 可选，默认 `gpt-4.1-mini`。
- `OPENAI_BASE_URL`: 可选，默认 `https://api.openai.com/v1`，可指向兼容 Chat Completions 的服务。
- `MAX_DIFF_CHARS`: 可选，默认 `120000`。
- `GITHUB_TOKEN` / `GITLAB_TOKEN` / `GITCODE_TOKEN`: 可选，私有 PR/MR 或发布评论时需要。

前端也可以临时输入平台 token；后端不会持久化 token。

## API

### `POST /api/review`

```json
{
  "pr_url": "https://github.com/org/repo/pull/123",
  "scm_token": "optional-token",
  "model": "optional-model"
}
```

返回 `comments` 中的每条意见都包含 `file_path`、`line`、`category`、`severity`、`body` 和 `publishable`。只有 `publishable=true` 的意见会被前端默认选中。

### `POST /api/publish`

```json
{
  "pr_url": "https://github.com/org/repo/pull/123",
  "scm_token": "token-with-comment-permission",
  "comments": []
}
```

GitHub 使用 Pull Request Review Comment；GitLab 使用 Merge Request Discussion position；GitCode 使用 v5 Pull Request comments 的 `path` + `position` 行级评论。

## 注意

- 行级发布依赖平台 API 对 diff position 的校验；如果 PR 被更新，旧 review 结果可能需要重新生成。
- 二进制文件或没有文本 patch 的文件会被跳过。
- 模型输出会被校验到实际 diff 的新行号；不在 diff 可评论行内的意见只展示，不自动发布。
