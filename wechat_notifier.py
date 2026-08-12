"""企业微信群机器人 — Markdown 消息构建与发送。

PRD §4.1 / §6.3
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

import requests

from config import SCORE_LABELS_CN
from models import AnalyzedProject

LOGGER = logging.getLogger(__name__)

RETRY_DELAYS = (10, 10, 10)  # PRD §6.3: 重试 3 次，间隔 10s
MAX_MARKDOWN_BYTES = 4096  # 企微 markdown content 上限

DIFFICULTY_CN = {"low": "入门友好", "medium": "需要折腾", "high": "硬核"}


@dataclass(slots=True)
class PushResult:
    ok: bool
    skipped: bool = False
    message: str = ""


def _truncate_bytes(text: str, limit: int) -> str:
    """按 UTF-8 字节截断，避免切坏多字节字符。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[: limit - 3].decode("utf-8", errors="ignore") + "..."


def build_markdown(project: AnalyzedProject, report_date: date) -> str:
    """构建企微 Markdown 消息（PRD §4.1）。

    刻意只放核心摘要，详情引流至 HTML 报告（DESIGN.md Don'ts）。
    """
    repo, analysis = project.repo, project.analysis
    scores = analysis.scores.as_dict()

    parts = [
        f"## 🔥 GitHub AI 日报 — {report_date.strftime('%Y-%m-%d')}",
        "",
        f"### [{repo.full_name}]({repo.html_url})",
        "",
        f"⭐ **推荐指数**: {analysis.rating}/5 | 🎯 **上手难度**: "
        f"{DIFFICULTY_CN.get(analysis.difficulty, analysis.difficulty)}",
        "",
        f"> {analysis.one_liner}",
        "",
    ]

    if analysis.highlights:
        parts.append("**核心亮点**：")
        parts += [f"- {h}" for h in analysis.highlights[:3]]
        parts.append("")

    if analysis.target_audience:
        parts += [f"**适合谁**：{analysis.target_audience}", ""]

    parts += [
        f"📊 **综合评分**: <font color=\"info\">{analysis.scores.total}</font>/100",
        f"- {SCORE_LABELS_CN['utility']} {scores['utility']} | "
        f"{SCORE_LABELS_CN['problem_solving']} {scores['problem_solving']}",
        f"- {SCORE_LABELS_CN['popularity']} {scores['popularity']} | "
        f"{SCORE_LABELS_CN['nas_usability']} {scores['nas_usability']}",
        "",
    ]

    if analysis.degraded:
        parts += [
            f"<font color=\"warning\">⚠️ AI 分析降级：{analysis.degrade_reason}</font>",
            "",
        ]

    links = []
    if project.report_url:
        links.append(f"📄 [查看完整分析报告]({project.report_url})")
    links.append(f"[GitHub 仓库]({repo.html_url})")
    parts.append(" | ".join(links))

    return _truncate_bytes("\n".join(parts), MAX_MARKDOWN_BYTES)


def build_empty_markdown(report_date: date) -> str:
    """PRD §6.4 空结果播报。"""
    return (
        f"## 🌙 GitHub AI 日报 — {report_date.strftime('%Y-%m-%d')}\n\n"
        "今日无新 AI 项目发现。\n\n"
        "<font color=\"comment\">近 3 天内符合条件的项目都已推送过，明天见。</font>"
    )


class WeChatNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: int = 20,
        session: requests.Session | None = None,
        retry_delays: tuple[int, ...] = RETRY_DELAYS,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.session = session or requests.Session()
        self.retry_delays = retry_delays

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send_markdown(self, content: str) -> PushResult:
        """发送 Markdown 消息。未配置 webhook 时跳过而非报错。"""
        if not self.enabled:
            LOGGER.warning("WECHAT_WEBHOOK_URL 未配置，跳过推送（报告与归档不受影响）")
            return PushResult(ok=False, skipped=True, message="webhook 未配置")

        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        last_error = ""

        for attempt in range(len(self.retry_delays) + 1):
            try:
                resp = self.session.post(self.webhook_url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"网络错误: {exc}"
                LOGGER.warning("企微推送失败 (%d/%d): %s", attempt + 1,
                               len(self.retry_delays) + 1, last_error)
            else:
                if resp.ok:
                    try:
                        data = resp.json()
                    except ValueError:
                        data = {}
                    errcode = data.get("errcode", -1)
                    if errcode == 0:
                        LOGGER.info("企微推送成功")
                        return PushResult(ok=True)
                    last_error = f"errcode={errcode} errmsg={data.get('errmsg')} raw={resp.text[:300]}"
                    LOGGER.error("企微返回非 0 errcode: %s", last_error)
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                    LOGGER.warning("企微推送失败 (%d/%d): %s", attempt + 1,
                                   len(self.retry_delays) + 1, last_error)

            if attempt < len(self.retry_delays):
                time.sleep(self.retry_delays[attempt])

        return PushResult(ok=False, message=last_error)

    def push_project(self, project: AnalyzedProject, report_date: date) -> PushResult:
        return self.send_markdown(build_markdown(project, report_date))

    def push_empty(self, report_date: date) -> PushResult:
        return self.send_markdown(build_empty_markdown(report_date))
