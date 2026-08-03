# Windows Worker 交接文档

本文档给负责 Windows 环境的开发者或 AI 编程助手使用。目标是让 Windows Worker 领取服务器上的桌面宠物任务，在本机打包 Windows `.exe`，再把结果上传回服务器。

## 当前项目状态

- 代码目录：`desktop-pet/`
- 服务端：Linux Docker，默认地址为 `http://<服务器IP>:8000`
- Windows Worker：`desktop-pet/worker/worker.py`
- 任务流程：浏览器上传照片 → AI 生成动作资源 → Worker 打包 exe → 浏览器下载
- 服务端不会接收 Windows 入站连接；Worker 只需要主动访问服务器的 HTTP API。
- API Key、Worker Token、Admin Token 都不能提交到 Git。

## 第一次配置 Windows Worker

在 PowerShell 中进入 worker 目录：

```powershell
cd desktop-pet\worker
.\install_worker.ps1
```

安装脚本会创建 `.venv`，安装 `requests` 和 `PyInstaller`，并创建本地 `worker/.env`。

编辑 `worker/.env`：

```dotenv
PET_SERVER_URL=http://<服务器IP>:8000
WORKER_TOKEN=<从服务器 server/.env 复制的 WORKER_TOKEN>
POLL_SECONDS=5
```

然后检查连接：

```powershell
.\check_connection.ps1
```

成功时应看到：

```text
服务器连接正常
Worker 鉴权正常
```

启动 Worker：

```powershell
.\run_worker.ps1
```

不要把 `worker/.env` 提交到 Git。仓库已经忽略该文件。

## 服务端配置

服务器的 `server/.env` 必须包含：

```dotenv
BUILD_MODE=worker
WORKER_TOKEN=<随机长字符串>
```

修改后，在服务器执行：

```bash
cd /home/workspace/code/skills/desktop-pet/server
docker compose -p desktop-pet up -d --build
```

Worker Token 必须和 Windows 的 `worker/.env` 完全一致，且不能复用前端 AI 配置使用的 `ADMIN_TOKEN`。

## 验收流程

1. 打开服务器网页。
2. 在 AI 配置面板填入 API Key 并启用 AI。
3. 上传一张或多张宠物照片。
4. 勾选“同时请求生成 Windows exe”。
5. 保持 PowerShell 中的 Worker 运行。
6. 任务状态应从 `ready_for_build` 变为 `building`，最后变为 `ready`。
7. 浏览器下载链接应显示为 Windows exe。

如果 AI 没有配置，Worker 仍然可以把照片动画兜底资源打包成 exe；这只能验证打包链路，不能验证 AI 动作帧质量。

## 给 Windows 上 AI 助手的工作提示

可以把下面内容直接交给 Windows 上的 AI 编程助手：

```text
你正在维护 desktop-pet 项目。先阅读 WINDOWS_WORKER_HANDOFF.md、README.md、worker/worker.py 和 server/app/package_builder.py。

你的第一目标是让 Windows Worker 端到端工作：
1. 运行 worker/install_worker.ps1；
2. 在 worker/.env 填写服务器地址和 Worker Token，不要把密钥写入代码或提交；
3. 运行 worker/check_connection.ps1；
4. 运行 worker/run_worker.ps1；
5. 从网页提交一个勾选了 exe 的宠物生成任务；
6. 检查 Worker 日志、服务器任务状态和最终 exe 是否可运行。

如果失败，按顺序检查：服务器地址可达性、服务器 /healthz、Worker Token、/api/worker/jobs/next、PyInstaller、pet_config.json、assets 目录和生成 exe 的启动错误。

完成验收后，再改进 Windows 体验，例如任务计划程序自启动、日志文件、托盘图标、exe 图标和代码签名。所有改动必须保留现有 API 协议，并补充测试；不要提交 .env、API Key 或 Worker Token。
```

## 常见问题

### `401 Worker token 无效`

服务器和 Windows 的 `WORKER_TOKEN` 不一致。重新复制服务器 `.env` 中的值，不要复制 `ADMIN_TOKEN`。

### `503 Windows Worker 未配置 WORKER_TOKEN`

服务器仍处于旧配置，或重启后没有加载 `.env`。确认 `BUILD_MODE=worker`、`WORKER_TOKEN` 非空，然后重新执行 Docker Compose 命令。

### Worker 能连接，但一直没有任务

网页任务必须勾选生成 exe；未勾选的任务只会生成 zip。也可以检查任务是否已经处于 `ready` 而不是 `ready_for_build`。

### PyInstaller 失败

在 Windows Worker 目录重新执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Worker 会显式收集 Pillow 的原生扩展，并在上传 exe 前运行 `--self-test`。如果旧版 exe 报 `ImportError: cannot import name '_imaging' from 'PIL'`，说明它是在修复前生成的，需要让 Worker 使用最新代码重新领取并生成任务。也可以先验证当前环境：

```powershell
.\.venv\Scripts\python.exe -c "from PIL import Image; import PIL._imaging; print('Pillow OK')"
```

然后保留 Worker 的完整错误日志。不要删除服务器任务目录，因为其中包含复现任务所需的资源包。
