# GitHub Star Digest

每天固定时间统计前一天 GitHub 上 Star 增长最快的公开项目，并推送到企业微信群机器人。

数据源使用 GH Archive 的公开小时归档文件。统计口径是：目标日期内 `WatchEvent` 最多的仓库，并按 `COUNT(DISTINCT actor.id)` 排名。

## 当前方案

服务不再依赖 Google Cloud、BigQuery 或服务账号。运行流程是：

```text
本机定时任务 -> 下载昨天 24 个 GH Archive .json.gz 文件 -> 统计 WatchEvent -> GitHub API 补信息 -> 企业微信推送
```

## 流量控制

服务内置三层保护：

1. 推送前可用 `--estimate-only` 对 24 个小时文件发 `HEAD` 请求，只读取文件大小，不下载正文。
2. 下载时实时统计压缩包字节数，超过 `ARCHIVE_MAX_DOWNLOAD_BYTES` 会中止，默认 `6GiB`。
3. 本服务记录本月已下载量，超过 `ARCHIVE_MONTHLY_DOWNLOAD_BUDGET_BYTES` 会中止，默认 `180GiB`。

这套方案没有 BigQuery 查询费用，但会消耗服务器入站流量和运行时间。如果你的服务器套餐有月流量限制，请把月度预算调低。

## 准备

需要：

- 企业微信群机器人 Webhook URL。
- 可选 GitHub token，用于避免 GitHub API 低频率限制。

复制配置：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
```

## 上线前估算

只估算下载量，不下载正文、不推送微信：

```bash
docker compose run --rm github-star-digest python -m app.main --once --estimate-only
```

输出里看到：

```text
within_free_guard=yes
```

再启动长期服务。

## 部署到本机服务器

构建并启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

默认每天北京时间 09:00 推送昨天的榜单。容器配置了 `restart: always`，服务器重启后会自动恢复。若要确保 Docker 服务本身开机启动：

```bash
sudo systemctl enable --now docker
```

## 立即推送一次

```bash
docker compose run --rm github-star-digest python -m app.main --once
```

指定统计日期：

```bash
docker compose run --rm github-star-digest python -m app.main --once --date 2026-06-01
```

如果当天已经推送过，需要重新发送：

```bash
docker compose run --rm github-star-digest python -m app.main --once --force
```

## 主要环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WECOM_WEBHOOK_URL` | 空 | 企业微信群机器人 Webhook |
| `GITHUB_TOKEN` | 空 | 可选 GitHub API token |
| `TIMEZONE` | `Asia/Shanghai` | 统计和推送时区 |
| `RUN_TIME` | `09:00` | 每天运行时间 |
| `ARCHIVE_BASE_URL` | `https://data.gharchive.org` | GH Archive 文件地址 |
| `ARCHIVE_MAX_DOWNLOAD_BYTES` | `6GiB` | 单次任务最大下载量 |
| `ARCHIVE_MONTHLY_DOWNLOAD_BUDGET_BYTES` | `180GiB` | 本服务月度下载预算 |
| `TOP_LIMIT` | `10` | 最终推送数量 |
| `CANDIDATE_LIMIT` | `50` | 候选项目数量 |
| `MIN_UNIQUE_STARGAZERS` | `5` | 最小独立 Star 用户数 |
| `NOTIFY_DRY_RUN` | `false` | 只打印日志，不推送微信 |

## 测试

源码方式需要 Python 3.12+：

```bash
python -m unittest discover -s tests
```

当前服务器默认 `python` 是 3.6，建议用 Docker 构建测试：

```bash
docker build . -t github-star-digest:latest
```

