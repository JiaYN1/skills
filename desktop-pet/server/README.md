# Docker 服务端

服务端提供一个简单的浏览器页面和 API：

1. 用户上传 1-8 张照片。
2. 服务端把原图作为身份参考交给 OpenAI-compatible 图像服务，并在提示词中要求 AI 直接输出透明背景动作帧；服务端只做 PNG、alpha、主体缩放和落地基线校验，不再使用本地分割模型。
3. 服务端生成资源包 zip。
4. 如果勾选 exe，Windows Worker 从服务端领取资源包，在 Windows 上用 PyInstaller 打包并回传 exe。

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

## AI 配置

默认配置指向 OpenAI-compatible 图片接口：

```dotenv
AI_API_BASE_URL=https://api.openai.com/v1
AI_API_KEY=...
AI_IMAGE_MODEL=gpt-image-1
POSE_CONSISTENCY=true
ANIMATION_MODE=hybrid
ANIMATION_FPS=12
WALK_FRAME_COUNT=12
SLEEP_FRAME_COUNT=10
```

也可以把 `AI_API_BASE_URL` 指向内部网关或其他兼容服务。服务端会优先请求图片编辑接口 `/images/edits`，没有参考图时使用 `/images/generations`，并兼容 base64 与 URL 两种响应。

AI 生成会把用户图片发送到配置的图像服务；生产环境应补充登录、限流、审计、对象存储和隐私告知。

## 动画输出

`hybrid` 是默认格式：资源包同时包含透明逐帧 PNG、`animation.json` 和 `skeleton.json`。走动默认使用 12 个细分接触/下压/经过/抬升姿态，睡觉默认使用 10 个呼吸与轻微抽动姿态；每个动作可以在管理面板中设置 1-12 帧。`skeleton.json` 是 sprite-backed root rig：它保存 root、body、head、前后腿和尾巴的时间线，适合后续接入真正的部位分割/蒙皮引擎。

背景移除优先由启用透明背景的 AI 提示词执行。服务端不会安装或调用本地分割模型；兼容网关偶尔返回不透明动作帧时，服务端会用 Pillow 尝试移除连接在边缘的简单纯色背景，不会因为单帧缺 alpha 让整单失败，也不使用 rembg。复杂背景仍建议更换支持原生透明输出的图像模型或网关。

某些兼容图像网关会把请求的 8 帧返回成一张 4×2/2×4 contact sheet。服务端会在归一化图片前拆分这种返回，避免把整张拼图当成单帧；已生成的旧资源包需要重新生成。PNG 本身是逐帧资源，浏览器或图片查看器不会自动播放，动画预览请运行资源包中的 `pet_runtime.py`，或运行打包后的 exe。

提示词会明确启用透明背景：只保留宠物主体，优先输出带真实透明 alpha 通道的 RGBA PNG，不要白底、绿底、棋盘格、房间、地面或阴影。`gpt-image-1`/`gpt-image-1.5` 会请求透明 PNG；`gpt-image-2` 为兼容接口不会发送其不接受的 `background=transparent` 参数，但仍通过提示词要求透明 alpha，并在必要时约定无纹理纯白兼容背景供服务端清理。

动画默认使用走动 12 帧、睡觉 10 帧、12 FPS。服务端会把同一动作的所有帧统一到相同的透明画布、主体比例、水平中心和落地基线；AI 提示词也会锁定镜头、缩放、光线与脚底位置，减少逐帧漂移。Windows runtime 更新同一个 Tk 图片对象，不再删除/重建画布项，避免透明窗口闪烁。

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

Windows 端安装脚本、连接自检和 AI 交接提示见项目根目录的 [WINDOWS_WORKER_HANDOFF.md](../WINDOWS_WORKER_HANDOFF.md)。
