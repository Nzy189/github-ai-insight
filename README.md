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

完整的本地测试方案见 **[LOCAL_TESTING.md](LOCAL_TESTING.md)**。

正式部署：

```bash
cp .env.example .env
docker compose up -d
```

---

## 工作流

```
GitHub Search API  →  SQLite 去重  →  LLM 分析打分  →  选出最高分  →  HTML + 企微 + 归档
   topic:ai/llm       full_name         结构化 JSON       加权总分
   近 3 天 Top 5      已推送则跳过      四维评分
```

**评分权重**：实用性 35% · 解决问题 30% · 受欢迎程度 25% · NAS 可用性 10%

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
python main.py --list               # 查看数据库最近记录
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
| `LLM_BASE_URL` | 是 | `https://api.openai.com/v1` | OpenAI 兼容端点 |
| `LLM_MODEL` | 是 | `gpt-4o` | 模型名 |
| `LLM_PROVIDER` | 否 | `openai` | `openai` 或 `anthropic` |
| `GITHUB_TOKEN` | 否 | 空 | 不填走匿名，60 次/小时 |
| `WECHAT_WEBHOOK_URL` | 否 | 空 | 不填则跳过推送 |
| `REPORT_BASE_URL` | 否 | `http://localhost:8080/reports` | **必须改成 NAS 实际地址** |
| `EXECUTION_TIME` | 否 | `12:00` | 按 `TIMEZONE` 解释 |
| `TIMEZONE` | 否 | `Asia/Dubai` | |
| `CANDIDATE_COUNT` | 否 | `5` | 初筛候选数 |
| `SEARCH_DAYS` | 否 | `3` | 搜索近 N 天 |
| `MIN_STARS` | 否 | `10` | Star 门槛 |
| `NOTIFY_EMPTY` | 否 | `false` | 无候选时是否推送 |
| `DATA_DIR` | 否 | `./data` | |
| `LOG_LEVEL` | 否 | `INFO` | |

---

## 文件结构

```
├── config.py              配置加载与校验（pydantic-settings）
├── models.py              领域模型：Repo / Analysis / Scores / AnalyzedProject
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
├── tests/                 150 个单元与端到端测试
├── scripts/               本地一键验证脚本
├── Dockerfile
├── docker-entrypoint.sh   PUID/PGID 降权（NAS 权限适配）
└── docker-compose.yml
```

数据产物：

```
data/
├── github_ai_insight.db
├── reports/YYYY-MM-DD-{owner}_{repo}.html
└── archive/YYYY-MM/YYYY-MM-DD-{owner}_{repo}.md
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
| 去重后无候选 | 静默跳过；`NOTIFY_EMPTY=true` 时推送"今日无新发现" |

降级产生的报告页顶部会显示橙色虚线警告横幅。

---

## 报告页

自包含单文件 HTML，**零外部请求**（无 CDN、无外链字体、无 JS），断网可正常浏览。

- 暗色主题，背景 `#0A0A0B`，卡片 `#111113`
- 移动端优先：< 640px 单列堆叠，触摸目标 ≥ 44px
- 评分环 SVG 渐变（绿/黄/红三段）+ 四维进度条
- `@media print` 切白底黑字便于存档
- `prefers-reduced-motion` 关闭动画
- LLM 输出经 HTML 转义后再渲染 Markdown，杜绝注入

访问：`http://{NAS_IP}:8080/reports`

---

## NAS 部署注意

1. `PUID` / `PGID` 改成 NAS 上 `id` 命令的实际输出，否则 `./data` 写不进去
2. `REPORT_BASE_URL` 改成局域网地址，否则企微里的链接在手机上打不开
3. `TZ` 与 `TIMEZONE` 保持一致
4. 健康检查探测 SQLite 可连接性，`docker compose ps` 显示 `healthy` 即正常
