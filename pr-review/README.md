# PR Review Service

把 `pr-review` skill 封装成可部署服务：前端输入 PR/MR 链接，后端拉取 diff，调用 OpenAI-compatible Chat Completions 生成结构化 review 意见，并可将选中的意见发布到识别到的 PR 代码行。

## 页面使用

1. 如果配置了访问密码，先在登录页输入密码。登录态默认有效期为 30 天。
2. 输入 PR/MR 链接；私有仓库或发布评论时，可在「平台 Token」中填写对应平台的 token，也可以使用服务端配置的 token。
3. 从「模型」下拉列表选择模型，无需手动输入。默认使用 `OPENAI_MODEL`；页面会从配置的模型服务获取其他可选模型。获取失败时会显示提示，仍可使用默认模型。
4. 点击「生成 Review」，查看问题总数、严重问题、建议、规范问题，以及每条意见的问题描述、修改建议和代码示例。
5. 检查并勾选要发布的意见，点击「发布选中意见」。可发布意见默认全选，不可发布意见会显示原因且无法勾选；发布后逐条显示结果。

生成 Review 后，需要点击发布按钮才会向平台提交评论。模型列表来自服务端配置的 `OPENAI_BASE_URL`，不是固定的模型名称列表；请选择支持 Chat Completions 的模型。

## 支持范围

- GitHub: `https://github.com/{owner}/{repo}/pull/{number}`
- GitLab/self-hosted GitLab: `https://{host}/{group}/{repo}/-/merge_requests/{iid}`
- GitCode: `https://gitcode.com/{owner}/{repo}/pull/{number}`，使用 GitCode v5 API

## 本地运行

需要 Python 3.10 或更高版本，Docker 镜像使用 Python 3.12。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少填写 `OPENAI_API_KEY`，按需设置模型服务地址、默认模型、访问密码和平台 token，然后启动：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 19986
```

打开 `http://localhost:19986`。

如果设置了 `ACCESS_PASSWORD`，首次打开会先进入密码页，输入正确密码后才能访问服务。

## Docker

```bash
cp .env.example .env
```

填写 `.env` 后，构建并启动：

```bash
docker build -t pr-review-service:local .
docker run -d --name pr-review-service --restart unless-stopped \
  -p 19986:19986 --env-file .env \
  pr-review-service:local
```

代码或配置更新后，重新构建并替换容器：

```bash
docker build -t pr-review-service:local .
docker stop pr-review-service
docker rm pr-review-service
docker run -d --name pr-review-service --restart unless-stopped \
  -p 19986:19986 --env-file .env \
  pr-review-service:local
curl --fail http://localhost:19986/api/health
```

健康检查应返回 `{"status":"ok"}`。重新打开或刷新页面即可加载新版前端。`.env` 不会打包进镜像，容器启动时通过 `--env-file` 注入配置。

## 环境变量

- `OPENAI_API_KEY`: 必填，用于生成 review。
- `OPENAI_MODEL`: 可选，默认 `gpt-4.1-mini`。
- `OPENAI_BASE_URL`: 可选，默认 `https://api.openai.com/v1`，可指向兼容 Chat Completions 的服务。
- `MAX_DIFF_CHARS`: 可选，默认 `120000`；小 PR 单次发给模型的 diff 上限。
- `LARGE_PR_DIFF_LINES`: 可选，默认 `500`；变更行数（新增 + 删除）超过该值时启用大 PR 策略。
- `LARGE_PR_MAX_FILES`: 可选，默认 `5`；大 PR 策略下最多检视的文件数。
- `LARGE_PR_FILE_MAX_CHARS`: 可选，默认 `60000`；大 PR 策略下单文件 diff 的上限。
- `LARGE_PR_CONCURRENCY`: 可选，默认 `2`；大 PR 逐文件检视的并发数。
- `DUPLICATE_SIMILARITY_THRESHOLD`: 可选，默认 `0.7`；判定"疑似重复意见"的正文相似度阈值，调到 `1.0` 相当于只信指纹。
- `ACCESS_PASSWORD`: 可选；设置后启用访问密码。
- `ACCESS_SESSION_SECRET`: 可选；用于签名登录 cookie，未设置时会基于 `ACCESS_PASSWORD` 派生。
- `ACCESS_SESSION_TTL_SECONDS`: 可选；登录态有效期，默认 `2592000` 秒（30 天）。
- `GITHUB_TOKEN` / `GITLAB_TOKEN` / `GITCODE_TOKEN`: 可选，私有 PR/MR 或发布评论时需要。

前端也可以临时输入平台 token；后端不会持久化 token。请求中提供的 token 优先于环境变量；GitCode 未配置 `GITCODE_TOKEN` 时会回退使用 `GITLAB_TOKEN`。

`ACCESS_SESSION_TTL_SECONDS` 显式配置时优先于默认值，最小有效值为 60 秒。已有配置中的 `43200` 仍表示 12 小时，要使用 30 天可改为 `2592000` 或删除该项。

## 大 PR 策略

变更行数（新增 + 删除）超过 `LARGE_PR_DIFF_LINES`（默认 500）时，一次把整个 diff 交给模型会挤压上下文、丢掉后面的改动，因此改为**逐文件检视**：

1. 对每个变更文件分级：接口定义（`.h`/`.hpp`/`.proto`/`api`、`routes`、`controllers` 等）、配置与依赖（`*.yaml`/`*.toml`/`Dockerfile`/`requirements*.txt` 等）优先于普通业务代码；
2. 测试、文档、锁文件、生成代码、第三方目录、资源与多语言文件不占用检视名额；
3. 同级文件按变更行数排序，最多取 `LARGE_PR_MAX_FILES`（默认 5）个；
4. 每个文件单独调用一次模型（默认并发 `LARGE_PR_CONCURRENCY=2`），单文件 diff 超过 `LARGE_PR_FILE_MAX_CHARS` 时只截断该文件。

返回结果的 `warnings` 会说明本次选了哪些文件（含分级）以及未检视的文件数量与原因；某个文件调用失败只跳过该文件，不影响其余文件。

## 重复意见识别

每次生成 review 前会读取 PR/MR 上已有评论，分两档处理：

**确定重复**（不会重复发布）

- 每条发布的意见都带隐藏指纹 `<!-- pr-review-id:... -->`（由文件路径、类别、问题描述生成，与行号无关，PR 更新后行号变化也不影响识别）；
- 指纹相同的意见标记 `already_posted=true`、`publishable=false`，页面显示"PR 上已存在相同意见"，不可选中；
- 对早期没有指纹的评论，会解析评论正文里的类别与"问题"描述重新计算指纹，因此同一行上的另一条不同意见不会被误判为重复；
- 发布接口在提交前会重新读取一次已有评论，避免页面数据过期导致重复提交。

**疑似重复**（由用户决定）

- 正文"问题描述"与已有评论高度相似（相似度 ≥ `DUPLICATE_SIMILARITY_THRESHOLD`，默认 `0.7`）时标记 `duplicate_suspect=true`；
- 这一档专门用来兜住**别人写的、或者 agent skill 流程写的**同类意见——它们既没有指纹，格式也不同（`【review】【类别】问题；修改建议：…`），只能靠正文比对；
- 这类意见**保持可发布**，页面显示"疑似已提过：<原评论链接>（勾选后仍会发布）"，默认**不勾选**，用户确认后勾选即可照常发布；
- 文件名已知但两边不同的意见不会算作疑似重复；正文过短（< 12 字，如 `compile`、`ci_pipeline`）的噪声评论不参与比对。

同一次生成内部重复的意见也会去掉。读取已有评论失败（例如缺少权限）时不会中断 review，只会在 `warnings` 里提示"本次不做重复校验"。

各平台的可用信息不同：GitHub 的 review comments 与 GitLab 的 discussions 都带文件路径和行号；**GitCode 的评论列表接口不返回文件路径**（行号在 `diff_position.start_new_line` 里），所以 GitCode 上"确定重复"依赖指纹、其余靠正文相似度。

## API

配置 `ACCESS_PASSWORD` 后，业务接口需携带登录得到的 `pr_review_session` cookie；未登录返回 HTTP 401。`/api/health` 和 `/api/login` 可直接访问。

### `GET /api/health`

返回 `{"status":"ok"}`，用于确认服务存活。

### `POST /api/login` / `POST /api/logout`

登录请求为 `{"password":"your-access-password"}`，成功返回 `{"status":"ok"}` 并设置登录 cookie；密码错误返回 HTTP 401。未配置访问密码时，登录返回 `{"status":"disabled"}`。

登出不需要请求体，成功返回 `{"status":"ok"}` 并清除登录 cookie。

### `GET /api/models`

从模型服务的 `/models` 接口获取模型列表，默认模型始终保留在列表首位，其余模型去重后排序。例如：

```json
{
  "default_model": "gpt-4.1-mini",
  "models": ["gpt-4.1-mini", "another-chat-model"]
}
```

未配置 API key、上游请求失败或返回格式不符合预期时，接口仍返回 HTTP 200，仅保留默认模型，并通过 `warning` 字段说明原因。模型服务 API key 只由后端使用。

### `POST /api/review`

```json
{
  "pr_url": "https://github.com/org/repo/pull/123",
  "scm_token": "optional-token",
  "model": "optional-model"
}
```

`scm_token` 和 `model` 可省略或传 `null`，分别使用服务端平台 token 和默认模型。

返回 `pr`（PR 信息）、`comments`（意见列表）、`summary`（数量统计和摘要）及 `warnings`（处理提示）。每条意见包含 `id`、`file_path`、`line`、`category`、`severity`、`message`、`suggestion`、`code_example`、`language`、`body`、`publishable`、`publish_warning`、`already_posted`、`existing_url`、`duplicate_suspect` 和 `duplicate_score`。严重程度为「严重」「建议」「规范」；只有 `publishable=true` 的意见可以被选中发布，其中 `already_posted=true` 表示该意见已确定存在于 PR/MR 上、不会重复发布，`duplicate_suspect=true` 表示疑似重复（前端默认不勾选，用户可自行勾选发布）。

### `POST /api/publish`

```json
{
  "pr_url": "https://github.com/org/repo/pull/123",
  "scm_token": "token-with-comment-permission",
  "comments": []
}
```

GitHub 使用 Pull Request Review Comment；GitLab 使用 Merge Request Discussion position；GitCode 使用 v5 Pull Request comments 的 `path` + `position` 行级评论。

将生成 Review 返回的选中意见完整传入 `comments`；空数组不会发布评论。返回 `results`，逐条包含 `id`、`file_path`、`line`、`status`、`url` 和 `error`，其中 `status` 为 `published`、`skipped` 或 `error`。发布需要具有评论权限的平台 token。

## 验证

在已安装依赖的 Python 环境中执行：

```bash
python -m unittest discover -s tests
```

## 注意

- 行级发布依赖平台 API 对 diff position 的校验；如果 PR 被更新，旧 review 结果可能需要重新生成。
- 二进制文件或没有文本 patch 的文件会被跳过。
- 发给模型的 diff 会为每条可评论新行生成 `line_anchor`，`line` 以同一行的 `[new:<数字>]` 为准；后端只在有效锚点能映射到当前 diff 时允许自动发布，缺失或无效锚点的意见只展示不推送。
- diff 超过 `MAX_DIFF_CHARS` 时会优先跳过测试文件，尽量保留业务代码；仍超长才截断。
