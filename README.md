# GitHub AI Insight

> 部署在 NAS 上的 Docker 自动化服务。每天定时抓取 GitHub 热门 AI/LLM 开源项目，
> 通过 LLM 深度分析打分，选出当日最高分项目，生成暗色主题 HTML 报告，并推送企业微信群机器人。

需求见 [GitHub-AI-Insight-PRD.md](GitHub-AI-Insight-PRD.md)，视觉规范见 [DESIGN.md](DESIGN.md)。

---

## 快速开始

**先本地跑通，再上 NAS。** 本地验证不需要任何 API Key：

```bash
python -m pip install -r requirements-dev.txt
python main.py --now --mock --open
```

完整的本地测试方案见 **[LOCAL_TESTING.md](LOCAL_TESTING.md)**，
NAS 上线步骤见 **[DEPLOY.md](DEPLOY.md)**（极空间 + Tailscale）。

正式部署：

```bash
cp .env.example .env
docker compose up -d
```

**改配置（换模型 / 换 API / 改推送地址）**：编辑 `.env`，然后

```bash
docker compose restart
```

`.env` 是以**文件**形式挂进容器的，应用在进程启动时读它，所以 `restart` 就能生效
——不需要 `up -d` 重建。唯一的例外是 `TIMEZONE` / `PUID` / `PGID` / `HTTP_PORT`
这四项（它们要在进程起来之前生效），改这些仍需 `docker compose up -d`。

改完看一眼日志确认新配置可用：

```bash
docker compose logs -f --tail 30
```

启动时会自动发一次最小 LLM 请求做自检，日志里出现 `启动自检通过 ✓` 就说明
新模型能用；配错了会直接打出错误原因，不用等到第二天收到一条全降级的日报。

---

## 工作流

```
GitHub Search API  →  SQLite 去重  →  LLM 分析打分  →  选出最高分  →  HTML + 企微 + 归档
   topic:ai/llm       full_name         结构化 JSON      今日候选 ∪ 候补池
   近 N 天 Top 5      已推送则跳过      四维评分
```

**评分权重**：实用性 35% · 解决问题 30% · 受欢迎程度 25% · NAS 可用性 10%

### 候补池

每天分析 5 个只推 1 个，落选的 4 个过了 GitHub 搜索窗口（`SEARCH_DAYS` 天）就再也不会出现在
候选里 —— 但它们的完整分析结果留在了库里。**选题时取「今日候选 ∪ 候补池」的全局最高分**：

```
第 1 天  85 / 84 / 83  →  推 85，84 和 83 进候补池
第 5 天  72 / 28 / 79  →  今天最高 79 打不过候补池里的 84  →  推 84
```

推过的项目立刻出池，候补池按分数从高到低自然排空。取用候补项目**不重新调用 LLM**
（分析结果现成），只花一次 GitHub 请求确认仓库还在，报告页顶部会标注「往期精选」。

今日候选为空时也会先看候补池，两边都空才回到「空结果策略」。

### 项目状态

抓取时会排除库里见过的一切 —— 已有分析结果的项目不再重复调用 LLM。

| 状态 | 含义 | 会被重新抓取 | 参与候补池竞争 |
|---|---|---|---|
| `pushed` | 已推送 | 否 | 否 |
| `degraded` | 用降级数据推送过 | 否 | 否 |
| `skipped` | 分析过、落选 | 否 | **是** |
| `failed` | 推送失败，未送达 | 否 | **是** |
| `rejected` | 低于 `REJECT_BELOW` | 否 | 否 |
| `retry` | 分析失败，值得再试 | **是** | 否 |

`retry` 是唯一会被重新抓回来分析的状态：降级时的 50 分是占位值，不代表项目差，
不该因为一次网络抖动就永久拉黑一个项目。

全场最高分（含候补池）低于 `REJECT_BELOW` 时**当天不推送** —— 宁可安静一天，
也不推垃圾。

---

## 命令行

```bash
python main.py                      # 常驻：定时调度 + 报告 HTTP 服务
python main.py --now                # 立即执行一次
python main.py --now --mock         # 本地假数据跑通全链路（零网络）
python main.py --now --dry-run      # 真实分析但不推送企微
python main.py --now --open         # 执行完用浏览器打开报告
python main.py --serve              # 只启动报告 HTTP 服务
python main.py --show-config        # 查看当前配置（密钥脱敏）
python main.py --test-llm           # 验证 LLM Key / 地址 / 模型是否可用
python main.py --list               # 查看数据库最近记录
```

容器里同样可用，换模型后手动验一下：

```bash
docker compose exec github-ai-insight python main.py --test-llm
docker compose exec github-ai-insight python main.py --show-config
docker compose exec github-ai-insight python main.py --now   # 立刻跑一次，不等定时
```

覆盖 `.env` 的临时参数（优先级更高）：

```bash
python main.py --now --model claude-sonnet-4-5 --candidates 10 --days 7
```

---

## 配置

全部通过 `.env` 加载，模板见 [.env.example](.env.example)。关键项：

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `LLM_API_KEY` | 是 | — | 不填则全部降级为 GitHub 元数据摘要 |
| `LLM_BASE_URL` | 是 | `https://api.openai.com/v1` | OpenAI 兼容端点，`.env.example` 里有 6 组服务商预设 |
| `LLM_MODEL` | 是 | `gpt-4o` | 模型名 |
| `LLM_PROVIDER` | 否 | `openai` | `openai` 或 `anthropic` |
| `GITHUB_TOKEN` | 否 | 空 | 不填走匿名，60 次/小时 |
| `WECHAT_WEBHOOK_URL` | 否 | 空 | 不填则跳过推送 |
| `REPORT_BASE_URL` | 否 | `http://localhost:8080/reports` | **必须是手机在任何网络下都能访问的地址**，见 [DEPLOY.md](DEPLOY.md) |
| `EXECUTION_TIME` | 否 | `12:00` | 按 `TIMEZONE` 解释 |
| `TIMEZONE` | 否 | `Asia/Dubai` | |
| `CANDIDATE_COUNT` | 否 | `5` | 初筛候选数 |
| `SEARCH_DAYS` | 否 | `3` | 搜索近 N 天。`.env.example` 里给的是 `15` —— 3 天实测太窄，常抓不满候选 |
| `MIN_STARS` | 否 | `10` | Star 门槛 |
| `REJECT_BELOW` | 否 | `65` | 低于此分直接淘汰，不进候补池 |
| `NOTIFY_EMPTY` | 否 | `false` | 无候选时是否推送 |
| `DATA_DIR` | 否 | `./data` | |
| `LOG_LEVEL` | 否 | `INFO` | |

---

## 文件结构

```
├── config.py              配置加载与校验（pydantic-settings）
├── models.py              领域模型：Repo / Analysis / Scores / Tldr / AnalyzedProject
├── db.py                  SQLite 建表、去重、UPSERT
├── github_client.py       Search API + README + 速率限制感知
├── ai_analyzer.py         LLM 调用 + JSON 解析 + 打分 + 降级
├── report_generator.py    Jinja2 渲染 HTML + Markdown 归档
├── wechat_notifier.py     企微消息构建与发送
├── report_server.py       内置 HTTP 服务（/reports 与 /health）
├── main.py                流程编排 + APScheduler + CLI
├── mock_data.py           本地测试假数据与假客户端
├── templates/
│   └── report.html.j2     自包含暗色报告模板
├── tests/                 259 个单元与端到端测试
├── scripts/               本地一键验证脚本
├── Dockerfile
├── docker-entrypoint.sh   PUID/PGID 降权（NAS 权限适配）
└── docker-compose.yml
```

数据产物：

```
data/
├── github_ai_insight.db          # WAL 模式；候补池的权威副本
├── reports/YYYY-MM-DD-{owner}_{repo}.html
└── archive/
    ├── YYYY-MM/…md               # 推送过的项目
    └── backlog/YYYY-MM/…md       # 落选项目（候补池的文本副本）
```

---

## 降级策略

任何外部依赖故障都不会中断流程，也不会让容器退出：

| 故障 | 行为 |
|------|------|
| LLM 超时 / 网络错误 | 重试 2 次（5s、15s），仍失败则降级 |
| LLM 返回非 JSON | 用 GitHub description 兜底，评分置 50，标 `degraded` |
| LLM Key 无效 / 余额不足 | 不重试，直接降级并在日志告警 |
| GitHub 速率限制 | 读 `X-RateLimit-Reset` 等待后重试；超 5 分钟则放弃本次 |
| GitHub Token 无效 | 自动降级为匿名请求继续 |
| 企微推送失败 | 重试 3 次（间隔 10s）；报告与归档照常生成 |
| 企微未配置 | 跳过推送，项目标记 `skipped`，配好后仍会推送 |
| 当日新项目分数都不高 | 从候补池取历史最高分项目顶上（不重新调 LLM） |
| 候补项目的仓库已删除 | 跳过它，回落到当日胜出者 |
| 去重后无候选 | 先看候补池；两边都空才静默跳过（`NOTIFY_EMPTY=true` 时推送"今日无新发现"） |

降级产生的报告页顶部会显示橙色虚线警告横幅。

---

## 报告页

自包含单文件 HTML，**零外部请求**（无 CDN、无外链字体、无 JS），断网可正常浏览。

- 暗色主题，背景 `#0A0A0B`，卡片 `#111113`
- 移动端优先：< 640px 单列堆叠，触摸目标 ≥ 44px
- 评分环 SVG 渐变（绿/黄/红三段）+ 四维进度条
- `@media print` 切白底黑字便于存档
- `prefers-reduced-motion` 关闭动画
- LLM 输出先做 HTML 转义，再用白名单扩展渲染 Markdown（显式排除 `attr_list`，
  否则 `{: onclick=... }` 语法能绕过转义给标签塞事件属性）

访问：`http://{Tailscale_IP}:8080/reports`（见 [DEPLOY.md](DEPLOY.md)）

---

## NAS 部署注意

1. `PUID` / `PGID` 改成 NAS 上 `id` 命令的实际输出，否则 `./data` 写不进去
2. `REPORT_BASE_URL` 用 Tailscale IP —— 局域网 IP 只在连家里 WiFi 时有效，
   用流量点开仍是死链。内置 HTTP 服务基于 Python `http.server` 且无鉴权，
   不应直接暴露公网
3. `TZ` 与 `TIMEZONE` 保持一致
4. 健康检查探测 SQLite 可连接性，`docker compose ps` 显示 `healthy` 即正常

### SQLite 在 NAS 上的三条注意

1. **`DATA_DIR` 必须是 NAS 本地路径，不能指向 SMB / NFS 挂载点。**
   数据库以 WAL 模式运行（NAS 断电时的恢复能力远好于默认 journal），
   而 WAL 在网络文件系统上不工作。启动日志里如果出现
   `无法启用 WAL` 的告警，说明路径落在了网络挂载上。
2. **别在容器运行时用 GUI 工具从电脑打开这个 `.db` 文件。**
   SQLite 在 SMB 上的文件锁是坏的，边写边开有损坏风险。
   要看数据用 `python main.py --list`，或先 `docker compose stop`。
3. **这个库是候补池的权威副本，值得纳入备份。**
   落选项目只存在于数据库里（`archive/backlog/` 下有文本副本可供人工恢复，
   但没有自动导入功能）。丢了库 = 丢了整个候补池。
