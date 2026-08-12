# GitHub AI Insight — 产品需求文档 (PRD)

> **版本**: v1.1 | **最后更新**: 2026-08-12
> **部署目标**: NAS (Docker) | **执行频率**: 每日 1 次

---

## 1. 项目概述

部署在 NAS 上的 Docker 自动化容器服务。每天定时抓取 GitHub 热门 AI/LLM 开源项目，通过 LLM 深度分析并打分，选出当日最高分项目，生成可视化 HTML 报告，并通过企业微信群机器人推送精炼简报。

---

## 2. 系统架构

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌────────────────┐
│  GitHub API │───▶│  初筛 + 去重  │───▶│  LLM 分析打分  │───▶│  推送 + 归档   │
│  (Search)   │    │  (SQLite)    │    │  (结构化JSON)  │    │ (企微 + HTML)  │
└─────────────┘    └──────────────┘    └───────────────┘    └────────────────┘
```

### 技术栈

| 层级 | 技术选型 |
|------|----------|
| 运行环境 | Python 3.11+ / Docker & Docker Compose |
| 数据源 | GitHub REST API (Search) |
| AI 引擎 | OpenAI / Anthropic 兼容 API（可切换模型） |
| 推送通道 | 企业微信群机器人 Webhook |
| 持久化 | SQLite + 本地 Markdown + HTML 报告 |
| 调度 | APScheduler |

---

## 3. 核心工作流

### 3.1 数据采集 (GitHub Scraper)

1. 调用 GitHub Search API，筛选近 **3 天内**创建或 Star 增长迅速、带有 `topic:ai` 或 `topic:llm` 的项目
2. 按 Star 数排序，取 **Top N**（N 可配置，默认 **5** 个）
3. 查询 SQLite 数据库进行**去重**：若 `full_name` 已存在且 `status=pushed`，则跳过
4. 去重后若结果为空，执行"空结果策略"（见 §6）

### 3.2 AI 分析 + 打分 (LLM Analyzer)

对去重后的每个候选项目（最多 5 个）：

1. 提取 `full_name`、`html_url`、`description`、`README.md`（截断至 8000 Token）
2. 调用 LLM API，要求返回**结构化 JSON**（见 §3.3）
3. 根据返回的评分字段计算加权总分

**评分维度**：

| 维度 | 权重 | 说明 |
|------|------|------|
| 实用性 | 35% | 是否解决实际工程问题、功能完整度 |
| 解决问题能力 | 30% | 痛点明确性、方案可行性 |
| 受欢迎程度 | 25% | Star 数、近期增长趋势 |
| NAS 可用性 | 10% | 是否支持 Docker/NAS 部署、资源占用是否合理 |

**总分 = 实用性×0.35 + 解决问题×0.30 + 受欢迎×0.25 + NAS可用性×0.10**

4. 选出**总分最高的 1 个项目**进入推送环节

### 3.3 LLM 输出格式

Prompt 要求 LLM 返回严格 JSON，格式如下：

```json
{
  "one_liner": "一句话核心价值（解决什么痛点）",
  "highlights": ["核心技术亮点1", "核心技术亮点2", "核心技术亮点3"],
  "target_audience": "适用场景与目标群体描述",
  "difficulty": "low|medium|high",
  "rating": 4,
  "rating_reason": "推荐/不推荐的理由",
  "detailed_intro": "面向普通用户的详细介绍，口语化风格，像安利一个好用的工具",
  "scores": {
    "utility": 85,
    "problem_solving": 80,
    "popularity": 90,
    "nas_usability": 70
  }
}
```

- 各项分数为 0-100 整数
- `difficulty` 仅限 `low` / `medium` / `high`
- `rating` 为 1-5 整数
- 解析失败时启用降级策略（见 §6.1）

### 3.4 推送 + 归档

对最终胜出的 1 个项目：

1. **生成 HTML 报告页** → 存入 `./data/reports/YYYY-MM-DD-{repo_name}.html`
2. **发送企微推送** → Markdown 消息 + 报告页链接
3. **写入 SQLite** → 记录完整分析数据
4. **生成 Markdown 归档** → 存入 `./data/archive/YYYY-MM/YYYY-MM-DD-{repo_name}.md`

---

## 4. 推送设计

### 4.1 企微 Markdown 消息

企业微信群机器人 Webhook 发送 Markdown 格式消息：

```markdown
## 🔥 GitHub AI 日报 — {YYYY-MM-DD}

### [{repo_name}]({html_url})

⭐ **推荐指数**: {rating}/5 | 🎯 **上手难度**: {difficulty}

> {one_liner}

**核心亮点**：
- {highlight_1}
- {highlight_2}
- {highlight_3}

**适合谁**：{target_audience}

📊 **综合评分**: {total_score}/100
- 实用性 {scores.utility} | 解决问题 {scores.problem_solving}
- 受欢迎 {scores.popularity} | NAS可用 {scores.nas_usability}

📄 [查看完整分析报告]({report_url}) | [GitHub 仓库]({html_url})
```

### 4.2 HTML 报告页

自包含 HTML 文件（内联 CSS，无外部依赖），托管于 NAS HTTP 服务。

**页面结构**：

| 区块 | 内容 |
|------|------|
| Header | 项目名 + GitHub 链接 + 日期 |
| Hero | 一句话核心价值（大字居中） |
| 评分卡片 | 四维评分雷达图或进度条 + 总分 |
| 详细介绍 | `detailed_intro` 渲染为排版良好的正文 |
| 技术亮点 | 列表展示 `highlights` |
| 快速上手 | 推荐指数 + 难度 + 理由 |
| Footer | "由 GitHub AI Insight 自动生成" + 项目链接 |

**设计规范**：
- 深色主题（适配 NAS Web UI 常见风格）
- 响应式布局（手机端可读）
- 无外部字体/CDN 依赖（离线可用）
- 单文件自包含（CSS 内联）

### 4.3 报告链接

HTML 报告通过 NAS 本地 HTTP 服务提供访问：
- 方案 A：挂载到 Nginx 容器的静态目录
- 方案 B：Python 内置 `http.server` 提供 `/reports` 路由
- 链接格式：`http://{NAS_IP}:{PORT}/reports/YYYY-MM-DD-{repo_name}.html`
- 此链接填入企微推送的 `[查看完整分析报告]` 处

---

## 5. 数据存储

### 5.1 SQLite 表结构

表名: `projects`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `repo_name` | TEXT UNIQUE | 仓库 full_name（去重键） |
| `repo_url` | TEXT | GitHub URL |
| `description` | TEXT | GitHub 原始描述（兜底用） |
| `language` | TEXT | 主语言 |
| `topics` | TEXT | GitHub topics（JSON 数组） |
| `stars` | INTEGER | 抓取时 Star 数 |
| `ai_summary` | TEXT | LLM 生成的结构化 JSON 原文 |
| `one_liner` | TEXT | 一句话总结 |
| `difficulty` | TEXT | low / medium / high |
| `rating` | INTEGER | 1-5 |
| `total_score` | REAL | 加权总分 |
| `report_path` | TEXT | HTML 报告文件路径 |
| `fetched_at` | TIMESTAMP | 抓取时间 |
| `pushed_at` | TIMESTAMP | 推送时间 |
| `status` | TEXT | pushed / failed / degraded / skipped |
| `error_message` | TEXT | 失败原因（如有） |

**说明**：数据量极小（每天 1-5 条），无需清理策略，长期保留作为历史记录。

### 5.2 文件归档

```
./data/
├── github_ai_insight.db          # SQLite 数据库
├── archive/                      # Markdown 归档
│   └── YYYY-MM/
│       └── YYYY-MM-DD-{owner}_{repo}.md
└── reports/                      # HTML 报告（供 HTTP 服务）
    └── YYYY-MM-DD-{owner}_{repo}.html
```

---

## 6. 错误处理与降级策略

### 6.1 LLM 调用失败

| 场景 | 行为 |
|------|------|
| API 超时/网络错误 | 重试 2 次，间隔 5s、15s（指数退避） |
| 返回非 JSON / 解析失败 | 用 GitHub `description` 填充 `one_liner`，评分全部置 50，标记 `status=degraded` |
| API Key 无效 (401) | 记录错误，跳过分析，推送纯 GitHub 元数据摘要，标记 `status=degraded` |
| 余额不足 (429/402) | 同上，并在日志中告警 |

### 6.2 GitHub API 失败

| 场景 | 行为 |
|------|------|
| 未认证 (401) | 使用匿名请求，日志提示配置 Token |
| 速率限制 (403) | 读取 `X-RateLimit-Reset` 头，等待至重置后重试 1 次；仍失败则跳过本次 |
| 无搜索结果 | 执行空结果策略（见 §6.4） |
| 网络错误 | 重试 2 次后跳过本次，记录错误 |

### 6.3 企微推送失败

| 场景 | 行为 |
|------|------|
| Webhook 超时/错误 | 重试 3 次，间隔 10s |
| 返回非 0 errcode | 记录完整响应，HTML 报告仍正常生成和归档 |
| URL 未配置 | 跳过推送，仅归档，日志警告 |

### 6.4 空结果策略

当日去重后无候选项目时（全部已推送过）：
- **默认行为**：静默跳过，不推送
- **可选行为**：推送一条"今日无新 AI 项目发现"（通过环境变量 `NOTIFY_EMPTY=true` 开启）

---

## 7. 配置管理

### 7.1 环境变量

通过 `.env` 文件 + `pydantic-settings` 加载：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `GITHUB_TOKEN` | 否 | (空=匿名) | GitHub Personal Access Token |
| `LLM_API_KEY` | 是 | - | LLM API Key |
| `LLM_BASE_URL` | 是 | - | LLM API Base URL |
| `LLM_MODEL` | 是 | `gpt-4o` | 模型名称 |
| `WECHAT_WEBHOOK_URL` | 否 | (空=跳过推送) | 企业微信群机器人 Webhook |
| `EXECUTION_TIME` | 否 | `12:00` | 每日执行时间 (GMT+4) |
| `TIMEZONE` | 否 | `Asia/Dubai` | 时区 |
| `CANDIDATE_COUNT` | 否 | `5` | 初筛候选数量 |
| `SEARCH_DAYS` | 否 | `3` | 搜索近 N 天项目 |
| `REPORT_BASE_URL` | 否 | `http://localhost:8080/reports` | HTML 报告访问地址前缀 |
| `NOTIFY_EMPTY` | 否 | `false` | 空结果时是否推送 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `DATA_DIR` | 否 | `./data` | 数据存储目录 |

### 7.2 命令行参数

```bash
# 立即执行一次
python main.py --now

# 指定模型执行
python main.py --now --model claude-3-5-sonnet-20241022

# 指定候选数量
python main.py --now --candidates 10

# 查看当前配置
python main.py --show-config
```

命令行参数优先级高于 `.env` 文件。

---

## 8. 项目文件结构

```
github-ai-insight/
├── .env.example               # 环境变量模板
├── config.py                  # pydantic-settings 配置管理
├── db.py                      # SQLite 初始化、去重查询、保存
├── github_client.py           # GitHub Search API 抓取 + 解析
├── ai_analyzer.py             # LLM 调用 + JSON 解析 + 打分
├── report_generator.py        # HTML 报告页生成
├── wechat_notifier.py         # 企微 Markdown 消息构建 + 发送
├── main.py                    # 主入口 + APScheduler 调度
├── requirements.txt           # Python 依赖
├── Dockerfile                 # 容器镜像
├── docker-compose.yml         # 编排配置
└── templates/
    └── report.html.j2         # HTML 报告 Jinja2 模板
```

---

## 9. Docker 部署

### 9.1 Dockerfile 要点

- 基础镜像: `python:3.11-slim`
- 创建非 root 用户运行
- 支持 `PUID`/`PGID` 环境变量（适配 NAS 权限）
- 设置 `TZ` 时区
- 健康检查: `HEALTHCHECK CMD python -c "import sqlite3; sqlite3.connect('/data/github_ai_insight.db')"`

### 9.2 docker-compose.yml 要点

```yaml
services:
  github-ai-insight:
    build: .
    container_name: github-ai-insight
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
    ports:
      - "8080:8080"  # HTML 报告 HTTP 服务
    healthcheck:
      test: ["CMD", "python", "-c", "import sqlite3; sqlite3.connect('/app/data/github_ai_insight.db')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

---

## 10. 交付清单

| # | 文件 | 说明 |
|---|------|------|
| 1 | `config.py` | 配置加载 + 校验 |
| 2 | `db.py` | SQLite CRUD + 去重 |
| 3 | `github_client.py` | GitHub 抓取 + 速率感知 |
| 4 | `ai_analyzer.py` | LLM 调用 + 结构化解析 + 打分 |
| 5 | `report_generator.py` | Jinja2 渲染 HTML 报告 |
| 6 | `wechat_notifier.py` | 企微消息格式化 + 发送 |
| 7 | `main.py` | 流程编排 + 调度 + CLI |
| 8 | `templates/report.html.j2` | HTML 报告模板 |
| 9 | `requirements.txt` | 依赖清单 |
| 10 | `Dockerfile` + `docker-compose.yml` | 容器部署 |
| 11 | `.env.example` | 配置模板 |
