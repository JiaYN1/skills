# Docker 服务端

服务端提供一个简单的浏览器页面和 API：

1. 用户上传 1-8 张照片。
2. 服务端保存任务并调用 OpenAI-compatible 图像服务，为待机、走动、睡觉、点击反应生成透明动作帧。
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

打开首页的“AI 配置（管理员）”面板，输入 `ADMIN_TOKEN` 后即可配置启用开关、API 地址、模型、API Key、动作帧数和参考图数量。Key 会保存在 `/data/settings.json`，不会返回原文；普通上传用户不需要管理令牌。

首次部署必须在 `.env` 中设置 `ADMIN_TOKEN`，不要使用公开的默认值。

## AI 配置

默认配置指向 OpenAI-compatible 图片接口：

```dotenv
AI_API_BASE_URL=https://api.openai.com/v1
AI_API_KEY=...
AI_IMAGE_MODEL=gpt-image-1
```

也可以把 `AI_API_BASE_URL` 指向内部网关或其他兼容服务。服务端会优先请求图片编辑接口 `/images/edits`，没有参考图时使用 `/images/generations`，并兼容 base64 与 URL 两种响应。

AI 生成会把用户图片发送到配置的图像服务；生产环境应补充登录、限流、审计、对象存储和隐私告知。

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
