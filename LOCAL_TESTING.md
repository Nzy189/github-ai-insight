# 本地测试指南

部署到 NAS 之前，先在开发机上把整条链路验证一遍。**全程不需要任何 API Key，不发起任何网络请求。**

---

## 0. 一键验证

```bash
python -m pip install -r requirements-dev.txt
```

Windows:

```bash
.\scripts\local_test.ps1
```

macOS / Linux:

```bash
bash scripts/local_test.sh
```

脚本依次执行：依赖安装 → 配置自检 → 单元测试 → Mock 全链路 → 去重验证 → 数据库检查 → 产物清单。全绿即可进入 Docker 部署。

> **编辑 `local_test.ps1` 时注意**：这个文件必须保存为 **UTF-8 with BOM**。
> Windows PowerShell 5.1 读 `.ps1` 时默认按系统 ANSI（中文系统即 GBK）解码，
> 无 BOM 的 UTF-8 会导致中文变乱码，并且乱码字节可能吞掉字符串的结束引号，
> 报出看起来毫不相关的 `字符串缺少终止符: "。`。
> PowerShell 7 默认 UTF-8 不受影响，所以这个问题只在 5.1 上出现。
> VS Code 里选 "UTF-8 with BOM"，或执行：
> ```powershell
> $p = ".\scripts\local_test.ps1"
> $c = [IO.File]::ReadAllText($p, [Text.UTF8Encoding]::new($false))
> [IO.File]::WriteAllText($p, $c, [Text.UTF8Encoding]::new($true))
> ```

下面是分步说明，用于定位问题。

---

## 1. 分层测试策略

系统有三个外部依赖：GitHub API、LLM API、企微 Webhook。本地测试按"替换掉几个"分成四层，**逐层放开**：

| 层级 | 命令 | GitHub | LLM | 企微 | 用途 |
|------|------|--------|-----|------|------|
| L1 单元测试 | `pytest` | 桩 | 桩 | 桩 | 逻辑正确性 |
| L2 Mock 全链路 | `--now --mock` | 假数据 | 假数据 | 打日志 | 端到端流程 + 报告样式 |
| L3 半真实 | `--now --dry-run` | **真实** | **真实** | 不发送 | 验证 Key 与 Prompt 效果 |
| L4 完整 | `--now` | 真实 | 真实 | **真实** | 上线前最后一步 |

---

## 2. L1 — 单元测试

```bash
python -m pytest -v
```

覆盖范围：

- **配置**：默认值、`HH:MM` 校验、CLI 覆盖优先级、密钥脱敏
- **打分**：权重和为 1、加权总分计算、边界值
- **JSON 解析**：裸 JSON / ```json 包裹 / 前后夹带废话 / 完全不是 JSON
- **数值夹紧**：rating 越界夹到 1-5、scores 夹到 0-100、difficulty 非法枚举回退
- **降级路径**：无 Key / 401 / 429 / 超时 / 解析失败，五种情况都不能抛异常
- **GitHub 客户端**：查询语法、双 topic 合并去重、5xx 重试、速率限制等待 `X-RateLimit-Reset`、401 降级为匿名
- **去重**：`pushed` / `degraded` 拦截，`skipped` / `failed` 放行
- **报告**：文件命名、自包含（无 CDN / 无外链字体 / 无 script）、XSS 转义、降级横幅、响应式与打印样式
- **企微**：消息结构、只取前 3 条亮点、4096 字节截断、非 0 errcode 判失败、重试次数
- **HTTP 服务**：`/health`、索引页、报告文件、空状态

看覆盖率：

```bash
python -m pytest --cov=. --cov-report=term-missing
```

---

## 3. L2 — Mock 全链路（最重要）

```bash
python main.py --now --mock
```

`--mock` 做了三件事：

1. `GitHubClient` → `MockGitHubClient`，返回 5 个内置假仓库
2. `LLMClient` → `MockLLMClient`，按仓库名返回预置回复
3. 企微 Session → `MockWeChatSession`，把消息完整打到日志而不发送

数据目录自动切到 `./data-local`，**不会污染真实的 `./data`**。

### 内置的边界情况

假数据刻意埋了坑，一次运行就能覆盖降级分支：

| 仓库 | 埋的坑 | 期望行为 |
|------|--------|----------|
| `localstack-ai/agentmesh` | 正常高分 | 胜出，总分 89.6 |
| `quietlabs/ragfoundry` | 正常 | 落选，记为 `skipped` |
| `nano-tools/whisperbox` | 回复被 ```json 包裹 | 正常解析 |
| `broken-json/llm-router` | 回复根本不是 JSON | 降级，评分置 50 |
| `edge-cases/promptforge` | rating=9、utility=120、difficulty="MEDIUM-HIGH" | 夹到 5 / 100 / medium |

运行后应该看到：

```
🏆 胜出: localstack-ai/agentmesh (89.6 分)
```

以及一段完整的企微消息预览。

### 检查产物

```bash
python main.py --list --data-dir ./data-local
```

```
data-local/
├── github_ai_insight.db
├── reports/2026-08-12-localstack-ai_agentmesh.html
└── archive/2026-08/2026-08-12-localstack-ai_agentmesh.md
```

### 在浏览器里看报告

```bash
python main.py --serve --data-dir ./data-local
```

打开 <http://localhost:8080/reports>。或者直接一步到位：

```bash
python main.py --now --mock --open
```

**报告页要检查的点**（DESIGN.md 规范）：

- 手机尺寸（Chrome DevTools 切到 375px）：单列、无横向滚动、H1 降到 28px、评分环缩到 64px、按钮高度 ≥ 44px
- 桌面：容器 800px 居中、评分卡 2×2
- 断网后刷新仍能正常显示（自包含，零外部请求）
- `Ctrl+P` 打印预览应为白底黑字

### 验证去重

连续跑两次，第二次应该换一个项目：

```bash
python main.py --now --mock   # → agentmesh
python main.py --now --mock   # → ragfoundry
```

跑满 5 次后，第 6 次应输出 `"reason": "去重后无候选项目"`。

重置：

```bash
# Windows
Remove-Item -Recurse -Force .\data-local
# macOS / Linux
rm -rf ./data-local
```

---

## 4. L3 — 接真实 API 但不推送

确认 Key 可用、Prompt 效果符合预期。

```bash
cp .env.example .env
```

填 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（以及可选的 `GITHUB_TOKEN`），然后：

```bash
python main.py --show-config     # 确认加载正确、密钥已脱敏
python main.py --now --dry-run
```

`--dry-run` 会真实抓取、真实调用 LLM、生成报告与归档，**但不推送企微**，并且把胜出项目标记为 `skipped` —— 所以正式上线时这个项目还会被重新推送一次，不会白白消耗掉。

省钱技巧：

```bash
python main.py --now --dry-run --candidates 1
```

常见问题：

| 现象 | 原因 |
|------|------|
| 全部项目显示"降级" | `LLM_API_KEY` / `LLM_BASE_URL` 有误，看日志里的具体错误码 |
| `候选 0 个` | 近 3 天没有满足 `MIN_STARS` 的新项目，把 `--days 7` 调大试试 |
| GitHub 403 | 匿名请求撞限，配置 `GITHUB_TOKEN` |
| Anthropic 接口报错 | `LLM_PROVIDER` 要改成 `anthropic` |

---

## 5. L4 — 完整链路

在 `.env` 里填上 `WECHAT_WEBHOOK_URL`：

```bash
python main.py --now
```

企微群里应收到消息。**注意 `REPORT_BASE_URL`**：默认是 `http://localhost:8080/reports`，在手机上点不开，必须改成 NAS 的实际地址，例如 `http://192.168.1.100:8080/reports`。

---

## 6. Docker 本地预演

上 NAS 之前先在开发机上用 Docker 跑一遍：

```bash
docker compose build
docker compose run --rm github-ai-insight python main.py --now --mock
```

确认容器内也能跑通后，启动常驻服务：

```bash
docker compose up -d
docker compose logs -f
curl http://localhost:8080/health
```

检查健康状态与文件权限：

```bash
docker compose ps
docker compose exec github-ai-insight ls -la /app/data
```

NAS 上需要把 `PUID` / `PGID` 改成 SSH 执行 `id` 得到的实际值，否则挂载目录会写不进去。

---

## 7. 调度验证

不想等到 12:00，把执行时间改成两分钟后：

```bash
# Windows PowerShell
$env:EXECUTION_TIME = (Get-Date).AddMinutes(2).ToString("HH:mm"); python main.py
```

```bash
# macOS / Linux
EXECUTION_TIME=$(date -d '+2 minutes' +%H:%M) python3 main.py
```

启动日志里会打印 `下次执行: ...`，到点后应看到完整流程日志。注意 `EXECUTION_TIME` 按 `TIMEZONE`（默认 `Asia/Dubai`）解释，不是本机时区。

---

## 8. 上线前检查清单

- [ ] `pytest` 全绿
- [ ] `--now --mock` 跑通，报告在手机尺寸下无横向滚动
- [ ] `--now --dry-run` 用真实 LLM 跑通，`detailed_intro` 读起来自然
- [ ] `REPORT_BASE_URL` 已改成 NAS 实际地址
- [ ] `docker compose up -d` 后 `/health` 返回 `ok`
- [ ] `PUID` / `PGID` 与 NAS 用户一致，`./data` 可写
- [ ] `.env` 已加入 `.gitignore`（模板已配置）
