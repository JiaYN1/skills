# GitCode PR Monitor

Docker Compose 可部署的 GitCode open PR 监控应用。后端每天 `Asia/Shanghai` 06:00 自动刷新，也可以在页面手动刷新；前端默认展示所有 open PR，并支持筛选严格大于 `STALE_DAYS` 的 PR。

## 配置

1. 复制环境变量文件：

```bash
cp .env.example .env
```

2. 设置 `.env`：

```bash
GITCODE_TOKEN=your-token
STALE_DAYS=5
TZ=Asia/Shanghai
PORT=3000
```

3. 编辑 `config/repos.json`：

```json
[
  {
    "owner": "your-owner",
    "repo": "your-repo",
    "label": "Your Repo"
  }
]
```

`GITCODE_TOKEN` 只在后端读取，不会进入前端构建产物。

## 本地运行

需要 Node.js 24+。

```bash
npm install
npm run dev:api
npm run dev
```

前端默认在 `http://localhost:5173`，API 在 `http://localhost:3000`。

## Docker Compose

```bash
docker compose up -d --build
```

SQLite 缓存在 `pr-monitor-data` volume 中，容器重启后保留。

## 部署

服务器需要 Docker、Docker Compose、SSH 和 `.env`。部署脚本会同步当前目录到服务器，并在远端执行构建启动：

```bash
scripts/deploy.sh user@server /opt/gitcode-pr-monitor
```

脚本不会同步本地 `.env`、`node_modules`、`dist` 和 `data`。

## API

- `GET /api/pulls`: 返回缓存的 open PR 列表。
- `GET /api/status`: 返回刷新状态、最近刷新时间、仓库刷新结果和阈值配置。
- `POST /api/refresh`: 触发一次手动刷新；如果已有刷新在运行，返回 `409`。
