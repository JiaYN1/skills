# B站动态微信推送

轮询 B 站 UP 主动态，发现新动态后推送到微信。当前支持两种微信通道：

- 企业微信群机器人：配置 `WECHAT_WEBHOOK_URL`
- Server酱 Turbo：配置 `SERVERCHAN_SENDKEY`

B 站动态没有官方 Webhook，因此项目用轮询实现准实时推送。`POLL_INTERVAL` 默认 30 秒，可以按需调到 10 秒以上；过低可能触发 B 站风控或 412。

## 配置

复制环境变量样例：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
BILI_UIDS=123456789,987654321
POLL_INTERVAL=30
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
# 或者使用 Server酱：
# SERVERCHAN_SENDKEY=SCTxxxx
```

UP 主 UID 可以从空间地址里取，例如 `https://space.bilibili.com/123456789`。

首次启动默认只记录当前已有动态，不会把历史动态全部推送。需要首次也推送接口第一页里的动态时，设置：

```env
NOTIFY_EXISTING_ON_FIRST_RUN=true
```

如果 B 站接口返回风控、权限或内容缺失，可以把浏览器登录后的 B 站 Cookie 填到 `BILI_COOKIE`。

## Docker 部署

构建并启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

停止：

```bash
docker compose down
```

状态文件会保存在 Docker 命名卷 `bili-wechat-data` 中，用于避免重启后重复推送。不要用 `docker compose down -v`，除非你确实想清空已推送记录。

## 开机自启

`docker-compose.yml` 已配置：

```yaml
restart: always
```

在服务器上执行一次 `docker compose up -d --build` 创建容器后，确保 Docker 服务开机启动：

```bash
sudo systemctl enable --now docker
```

之后服务器重启时 Docker 会自动拉起该容器。

## 本地测试

需要 Python 3.12+：

```bash
python -m unittest discover -s tests
```

不想真实发送微信消息时，可以在 `.env` 中设置：

```env
NOTIFY_DRY_RUN=true
```
