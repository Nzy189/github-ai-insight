# NAS 部署指南（极空间 + Tailscale）

目标形态：容器常驻在极空间上，每天定时跑一次，报告页通过 Tailscale 在
任何网络下都能从手机打开，且不对公网暴露任何端口。

---

## 0. 先决条件

- 极空间已安装 Docker（ZOS 的「Docker」应用）
- 极空间已加入你的 Tailscale 网络，`tailscale ip -4` 能拿到 `100.x.y.z`
- 本地已经跑通 `python main.py --now --dry-run`（见 [LOCAL_TESTING.md](LOCAL_TESTING.md)）

---

## 1. 把代码放到 NAS

SSH 进极空间（ZOS 设置里开启 SSH），选一个数据盘上的目录：

```bash
cd /tmp/zfsv3/samba/<你的用户名>     # 路径按你的实际共享目录调整
git clone https://github.com/Nzy189/github-ai-insight.git
cd github-ai-insight
```

后续更新只需要：

```bash
git pull && docker compose up -d --build
```

---

## 2. 确认 PUID / PGID

**这一步别跳过。** 填错的话容器写不进 `./data`，表现为启动后数据库创建失败。

```bash
id
```

记下 `uid=` 和 `gid=` 的数字，写进下一步的 `.env`。

> 极空间的用户模型和标准 Linux 不完全一致，别想当然填 1000。

---

## 3. 配置

```bash
cp .env.example .env
vi .env
```

必须改的四项：

| 变量 | 填什么 |
|---|---|
| `LLM_API_KEY` | 你的模型 API Key |
| `LLM_BASE_URL` / `LLM_MODEL` | 取消注释对应的服务商预设块 |
| `WECHAT_WEBHOOK_URL` | 企微群机器人 Webhook |
| `REPORT_BASE_URL` | `http://100.x.y.z:8080/reports` ← 极空间的 **Tailscale IP** |

另外两项按环境调整：

```
USE_SYSTEM_CERTS=false      # 容器里没有中间人代理，保持 false
TIMEZONE=Asia/Dubai         # 与你所在时区一致
```

`PUID` / `PGID` 写进 `.env`，compose 会通过变量插值读取。

---

## 4. 启动

```bash
docker compose up -d
docker compose logs -f --tail 50
```

日志里应该依次出现：

```
[entrypoint] 以 UID=xxx GID=xxx 运行: python main.py
已启用操作系统证书存储 (truststore)      ← 若 USE_SYSTEM_CERTS=true 才有
启动自检：验证 LLM 配置 glm-5.2 @ https://...
启动自检通过 ✓
报告 HTTP 服务已启动（后台）: http://0.0.0.0:8080/reports
调度已启动 | 每日 12:00 (Asia/Dubai)
下次执行: 2026-08-14 12:00:00+04:00
```

看到「启动自检通过 ✓」说明模型配置可用。如果这里报错，先解决再往下走。

---

## 5. 立刻跑一次，别等到明天

```bash
docker compose exec github-ai-insight python main.py --now
```

跑完检查三件事：

```bash
# 1. 企微群里收到消息了吗
# 2. 消息里的报告链接能在手机上打开吗（关掉 WiFi 用流量试）
# 3. 数据落库了吗
docker compose exec github-ai-insight python main.py --list
```

---

## 6. 验证 Tailscale 通路

在**手机流量**下（关掉 WiFi）打开：

```
http://100.x.y.z:8080/reports
```

打得开就说明整条链路通了。打不开的排查顺序：

1. 手机的 Tailscale 客户端是不是连着（图标是否为已连接状态）
2. 极空间上 `tailscale status` 里手机是否在列
3. NAS 本机 `curl http://127.0.0.1:8080/health` 是否返回 `ok`
4. 极空间的防火墙是否拦了 8080

> **注意**：如果 Tailscale 是以容器形式跑在极空间上、且没用 host 网络模式，
> 那么 NAS 宿主机本身可能并不在 tailnet 上，`100.x.y.z:8080` 会不通。
> 这种情况要么把 Tailscale 换成宿主机安装，要么给报告容器加
> `network_mode: service:tailscale`。

---

## 7. 日常运维

**改配置**（换模型、换 Key、换 Webhook）：

```bash
vi .env
docker compose restart
docker compose logs -f --tail 20     # 确认「启动自检通过 ✓」
```

`.env` 是以文件挂进容器的，`restart` 即生效。

**例外**——改下面这四项要 `docker compose up -d` 重建：

```
TIMEZONE   HTTP_PORT   PUID   PGID
```

**更新代码**：

```bash
git pull && docker compose up -d --build
```

**看数据**：

```bash
docker compose exec github-ai-insight python main.py --list
docker compose exec github-ai-insight python main.py --show-config
docker compose exec github-ai-insight python main.py --test-llm
```

---

## 8. 备份

数据库是候补池的权威副本（落选项目只存在于库里，`archive/backlog/`
下的 Markdown 只是人工恢复用的文本副本，没有自动导入功能）。

把 `./data` 目录纳入极空间的定期备份即可。

**不要在容器运行时用文件管理器或 SMB 直接打开 `github_ai_insight.db`**
—— SQLite 在网络文件系统上的锁是坏的，边写边开有损坏风险。要看数据用
`--list`，或先 `docker compose stop`。

---

## 常见问题

| 现象 | 原因 |
|---|---|
| 启动即退出，日志说 `/app/.env 是一个目录` | 首次启动时宿主机上没有 `.env`，Docker 自动建了目录。`docker compose down && rm -rf ./.env && cp .env.example .env` |
| 数据库创建失败 / 权限拒绝 | `PUID`/`PGID` 与实际用户不符，重新 `id` 确认 |
| 改了 `.env` 但没生效 | 改的是 `TIMEZONE`/`HTTP_PORT`/`PUID`/`PGID` 之一，需要 `up -d` 而非 `restart` |
| 日志出现 `无法启用 WAL` | `./data` 落在网络挂载上了，换成 NAS 本地路径 |
| 报告全是「降级数据」 | LLM 配置有问题，跑 `--test-llm` 看具体错误 |
| 手机上报告链接打不开 | `REPORT_BASE_URL` 不是 Tailscale IP，或手机没连 Tailscale |
| 企微收到消息但没有报告链接 | `REPORT_BASE_URL` 为空 |
