# Docker 服务端

服务端提供一个简单的浏览器页面和 API：

1. 用户上传 1-8 张照片。
2. 第一步调用 OpenAI-compatible 图像服务单独去除每张照片背景，浏览器展示透明主体预览。
3. 用户确认预览后，第二步才生成待机、走动、睡觉和点击动作帧，并展示生成的 PNG 资源预览。
4. 服务端生成资源包 zip。
5. 如果请求 exe，Windows Worker 从服务端领取资源包，在 Windows 上用 PyInstaller 打包并回传 exe。

## 启动 Docker 服务

```bash
cd desktop-pet/server
cp .env.example .env
# 编辑 .env，至少设置 AI_API_KEY 和 WORKER_TOKEN
docker compose up -d --build
```

打开 <http://localhost:8000>。

如果暂时不接 Windows Worker，可把 `.env` 中的 `BUILD_MODE` 改为 `archive`。这样用户仍然可以下载资源包 zip，但不会得到 exe。

## 前端配置 AI

打开首页的“AI 配置（管理员）”面板，输入 `ADMIN_TOKEN` 后即可配置启用开关、API 地址、模型、API Key、基础动作帧数、走动/睡觉帧数、FPS、参考图数量、姿态一致性和输出格式。Key 会保存在 `/data/settings.json`，不会返回原文；普通上传用户不需要管理令牌。

首次部署必须在 `.env` 中设置 `ADMIN_TOKEN`，不要使用公开的默认值。

## 两阶段预览流程

浏览器先调用 `POST /api/pets/prepare`。任务进入 `preview_processing`，AI 去背景完成后变为 `preview_ready`；`GET /api/jobs/{id}` 会返回 `preview_images`，图片通过受限的 `/api/jobs/{id}/preview/...` 地址预览。

用户确认预览后，浏览器调用 `POST /api/jobs/{id}/generate`，服务端才开始生成动作资源。任务完成后同一个任务状态会返回 `resource_preview_images`，可直接在页面查看各动作 PNG 帧，再下载 zip 或请求 Windows Worker。如果动作资源阶段失败但去背景预览仍然存在，可调用 `POST /api/jobs/{id}/resume` 复用已有预览继续生成，不会重复去背景。

资源包生成成功但 Windows Worker 打包 exe 失败时，任务会保留 zip；页面会显示“重试打包 Windows exe”，再次提交 `POST /api/jobs/{id}/build-exe` 即可重试。

## AI 配置

默认配置指向 OpenAI-compatible 图片接口：

```dotenv
AI_API_BASE_URL=https://api.openai.com/v1
AI_API_KEY=...
AI_IMAGE_MODEL=gpt-image-1
POSE_CONSISTENCY=true
ANIMATION_MODE=hybrid
ANIMATION_FPS=12
WALK_FRAME_COUNT=16
SLEEP_FRAME_COUNT=12
```

也可以把 `AI_API_BASE_URL` 指向内部网关或其他兼容服务。服务端会优先请求图片编辑接口 `/images/edits`，没有参考图时使用 `/images/generations`，并兼容 base64 与 URL 两种响应。

AI 生成会把用户图片发送到配置的图像服务；生产环境应补充登录、限流、审计、对象存储和隐私告知。

## 动画输出

`hybrid` 是默认格式：资源包同时包含透明逐帧 PNG、`animation.json` 和 `skeleton.json`。走动默认使用 16 个细分接触/下压/经过/抬升姿态，睡觉默认使用 12 个呼吸与轻微抽动姿态；每个动作可以在管理面板中设置 1-24 帧。`skeleton.json` 是 sprite-backed root rig：它保存 root、body、head、前后腿和尾巴的时间线，适合后续接入真正的部位分割/蒙皮引擎。

背景移除优先由启用透明背景的 AI 提示词执行。上传图和每一张 AI 动作帧都会经过 AI 去背景；服务端不会安装或调用本地分割模型。兼容网关仍返回不透明图片时，Pillow 只作为简单边缘清理回退，不会因为单帧缺 alpha 让整单失败，也不使用 rembg。复杂背景仍建议更换支持原生透明输出的图像模型或网关。

动作帧现在按“一个姿态、一个请求、一个 PNG”逐帧生成，不再依赖一次请求 `n=8/12` 后拆分 contact sheet；后续请求会参考上一帧，失败帧会保留序列位置。已生成的旧资源包需要重新生成。PNG 本身是逐帧资源，浏览器页面会展示全部帧，动画预览也可运行资源包中的 `pet_runtime.py` 或打包后的 exe。

提示词会明确启用透明背景：只保留宠物主体，优先输出带真实透明 alpha 通道的 RGBA PNG，不要白底、绿底、棋盘格、房间、地面或阴影。`gpt-image-1`/`gpt-image-1.5` 会请求透明 PNG；`gpt-image-2` 为兼容接口不会发送其不接受的 `background=transparent` 参数，但仍通过提示词要求透明 alpha，并在必要时约定无纹理纯白兼容背景供服务端清理。

动画默认使用走动 16 帧、睡觉 12 帧、12 FPS。服务端会把同一动作的所有帧统一到相同的透明画布、主体比例、水平中心和落地基线；AI 提示词也会锁定镜头、缩放、光线与脚底位置，并把上一帧作为连续性参考。Windows runtime 在 Windows 上使用原生 per-pixel alpha 分层窗口，避免色键透明导致的绿色边缘和闪烁。常态不随机走动，走动由右键菜单触发，点击宠物触发反应；闲置超时后进入睡眠并保持到下一次互动。

## Windows Worker

Linux 容器不能直接用 PyInstaller 产出 Windows exe。请在一台 Windows 机器上安装 Python 3.11+：

```powershell
cd desktop-pet\worker
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PET_SERVER_URL="https://你的服务器域名"
$env:WORKER_TOKEN="与 server/.env 相同的 token"
python worker.py
```

Worker 只通过带 token 的接口领取任务、下载资源包和上传 exe，不需要把 Windows 主机暴露给公网以外的端口。建议用 Windows 任务计划程序或 NSSM 将 Worker 作为后台服务运行。

EXE 构建默认不再使用 `--clean`，并只收集运行时需要的 Pillow 模块和二进制。需要排查 PyInstaller 缓存时可设置 `PYINSTALLER_CLEAN=true`；设置 `PET_PYINSTALLER_CACHE` 可指定持久构建目录。

Windows 端安装脚本、连接自检和 AI 交接提示见项目根目录的 [WINDOWS_WORKER_HANDOFF.md](../WINDOWS_WORKER_HANDOFF.md)。
