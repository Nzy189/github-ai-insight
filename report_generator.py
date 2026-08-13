"""HTML 报告页生成（Jinja2）+ Markdown 归档。

PRD §3.4 / §4.2 · 样式规范见 DESIGN.md
"""

from __future__ import annotations

import html
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import SCORE_LABELS_CN, SCORE_WEIGHTS
from models import AnalyzedProject

LOGGER = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
# 评分环由 80px 缩至 52px 并入判断条（见 2026-08-12 报告 IA 改造 spec）
RING_BOX = 52
RING_STROKE = 4
RING_RADIUS = (RING_BOX - RING_STROKE) // 2  # = 24
RING_CIRCUMFERENCE = round(2 * math.pi * RING_RADIUS, 2)  # = 150.8

DIFFICULTY_LABELS = {"low": "入门友好", "medium": "需要折腾", "high": "硬核"}
DIFFICULTY_CLASSES = {
    "low": "badge-difficulty-low",
    "medium": "badge-difficulty-mid",
    "high": "badge-difficulty-high",
}
# 评分渐变端点（DESIGN.md §2）
RING_GRADIENTS = {
    "high": ("#22C55E", "#4ADE80"),
    "mid": ("#F59E0B", "#FBBF24"),
    "low": ("#EF4444", "#F87171"),
}


def score_tier(score: float) -> str:
    """80+ high / 50-79 mid / <50 low。"""
    if score >= 80:
        return "high"
    if score >= 50:
        return "mid"
    return "low"


# 不能用 "extra" 合集：它捆绑了 attr_list，其 `{: ... }` 语法能给生成的标签
# 塞任意属性（如 onmouseover / onclick），而 html.escape 只处理 <>& —— 花括号
# 原样通过，转义拦不住它。detailed_intro 来自第三方 README 派生的模型文本，
# 一旦 attr_list 生效，被投毒的 README 就能在报告页上执行脚本。
# 因此这里显式列出所需扩展，永远不要换回 "extra"。
MARKDOWN_EXTENSIONS = ["fenced_code", "tables", "def_list", "abbr", "sane_lists", "nl2br"]


def render_markdown(text: str) -> str:
    """把 LLM 生成的 Markdown 转成 HTML。

    先做 HTML 转义再渲染 Markdown：LLM 输出属于不可信内容，
    不允许它往自包含报告里注入原始 HTML/脚本。
    扩展白名单排除了 attr_list（见 MARKDOWN_EXTENSIONS 注释）。
    """
    if not text or not text.strip():
        return ""
    escaped = html.escape(text, quote=False)
    return md.markdown(escaped, extensions=MARKDOWN_EXTENSIONS)


def _format_date(value: str) -> str:
    """把 GitHub ISO 时间戳格式化成 YYYY-MM-DD。"""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return value[:10]


class ReportGenerator:
    def __init__(
        self,
        reports_dir: Path,
        archive_dir: Path,
        *,
        report_base_url: str = "",
        model_name: str = "",
        template_dir: Path = TEMPLATE_DIR,
    ) -> None:
        self.reports_dir = Path(reports_dir)
        self.archive_dir = Path(archive_dir)
        self.report_base_url = report_base_url.rstrip("/")
        self.model_name = model_name or "未知"
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml", "j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    @property
    def archive_index_url(self) -> str:
        """归档首页地址 —— 由 report_base_url 去掉末段 /reports 反推。

        不用相对路径 "/"：报告是自包含单文件，可能被下载到本地用 file://
        打开，那时 "/" 会指向文件系统根目录。没配 base_url 就不给这个链接。
        """
        base = self.report_base_url
        if not base:
            return ""
        return base[: -len("/reports")] + "/" if base.endswith("/reports") else base + "/"

    # ------------------------------------------------------------ 模板上下文

    def build_context(self, project: AnalyzedProject, report_date: date) -> dict[str, Any]:
        repo, analysis = project.repo, project.analysis
        scores = analysis.scores.as_dict()
        total = analysis.scores.total

        dimensions = [
            {
                "key": key,
                "label": SCORE_LABELS_CN[key],
                "score": scores[key],
                "weight": f"{int(SCORE_WEIGHTS[key] * 100)}%",
                "css_class": f"score-{score_tier(scores[key])}",
            }
            for key in SCORE_WEIGHTS
        ]

        ring_start, ring_end = RING_GRADIENTS[score_tier(total)]
        ring_offset = round(RING_CIRCUMFERENCE * (1 - min(max(total, 0), 100) / 100), 2)

        tldr_rows = [
            {"label": label, "text": text}
            for label, text in (
                ("痛点", analysis.tldr.pain),
                ("怎么解决", analysis.tldr.solution),
                ("我能用吗", analysis.tldr.fit),
            )
            if text
        ]

        return {
            "repo": repo,
            "analysis": analysis,
            "dimensions": dimensions,
            "total_score": total,
            "total_score_int": int(round(total)),
            "ring_circumference": RING_CIRCUMFERENCE,
            "ring_offset": ring_offset,
            "ring_start": ring_start,
            "ring_end": ring_end,
            "tldr_rows": tldr_rows,
            "ring_box": RING_BOX,
            "ring_center": RING_BOX // 2,
            "ring_radius": RING_RADIUS,
            "ring_stroke": RING_STROKE,
            "difficulty_label": DIFFICULTY_LABELS.get(analysis.difficulty, analysis.difficulty),
            "difficulty_class": DIFFICULTY_CLASSES.get(
                analysis.difficulty, "badge-difficulty-mid"
            ),
            "intro_html": render_markdown(analysis.detailed_intro),
            "archive_url": self.archive_index_url,
            "from_backlog": project.from_backlog,
            "backlog_analyzed_at": project.backlog_analyzed_at,
            "report_date": report_date.strftime("%Y-%m-%d"),
            "created_display": _format_date(repo.created_at),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "model_name": "GitHub 元数据兜底" if analysis.degraded else self.model_name,
        }

    # ------------------------------------------------------------ HTML

    def report_filename(self, project: AnalyzedProject, report_date: date) -> str:
        return f"{report_date.strftime('%Y-%m-%d')}-{project.repo.slug}.html"

    def render_html(self, project: AnalyzedProject, report_date: date) -> str:
        template = self.env.get_template("report.html.j2")
        return template.render(**self.build_context(project, report_date))

    def write_report(self, project: AnalyzedProject, report_date: date) -> tuple[Path, str]:
        """生成 HTML 文件，返回 (路径, 可访问 URL) 并回填到 project。"""
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        filename = self.report_filename(project, report_date)
        path = self.reports_dir / filename
        path.write_text(self.render_html(project, report_date), encoding="utf-8")

        url = f"{self.report_base_url}/{filename}" if self.report_base_url else filename
        project.report_path = str(path)
        project.report_url = url
        LOGGER.info("HTML 报告已生成: %s", path)
        return path, url

    # ------------------------------------------------------------ Markdown 归档

    def build_markdown(self, project: AnalyzedProject, report_date: date) -> str:
        repo, analysis = project.repo, project.analysis
        scores = analysis.scores.as_dict()
        stars = "★" * analysis.rating + "☆" * (5 - analysis.rating)

        lines = [
            f"# {repo.full_name}",
            "",
            f"> {analysis.one_liner}",
            "",
            f"- **日期**: {report_date.strftime('%Y-%m-%d')}",
            f"- **仓库**: <{repo.html_url}>",
            f"- **Star**: {repo.stars:,} · **Fork**: {repo.forks:,}",
            f"- **语言**: {repo.language or '—'}",
            f"- **Topics**: {', '.join(repo.topics) or '—'}",
            f"- **推荐指数**: {stars} ({analysis.rating}/5)",
            f"- **上手难度**: {analysis.difficulty}",
            f"- **综合评分**: **{analysis.scores.total}/100**",
            "",
            "## 评分详情",
            "",
            "| 维度 | 权重 | 得分 |",
            "|------|------|------|",
        ]
        for key, weight in SCORE_WEIGHTS.items():
            lines.append(f"| {SCORE_LABELS_CN[key]} | {int(weight * 100)}% | {scores[key]} |")

        lines += ["", "## 技术亮点", ""]
        lines += [f"- {h}" for h in analysis.highlights] or ["- （无）"]

        lines += [
            "",
            "## 适合谁",
            "",
            analysis.target_audience or "（未提供）",
            "",
            "## 推荐理由",
            "",
            analysis.rating_reason or "（未提供）",
            "",
            "## 详细介绍",
            "",
            analysis.detailed_intro or "（未提供）",
            "",
            "---",
            "",
        ]
        if analysis.degraded:
            lines.append(f"> ⚠️ 降级数据：{analysis.degrade_reason}")
            lines.append("")
        if project.report_url:
            lines.append(f"HTML 报告: {project.report_url}")
            lines.append("")
        lines.append(f"由 GitHub AI Insight 自动生成 · {datetime.now():%Y-%m-%d %H:%M}")

        return "\n".join(lines) + "\n"

    def write_archive(self, project: AnalyzedProject, report_date: date,
                      *, subdir: str = "") -> Path:
        """写入 ./data/archive/[subdir/]YYYY-MM/YYYY-MM-DD-{owner}_{repo}.md

        subdir="backlog" 用于落选项目：SQLite 是候补池的唯一副本，
        NAS 上那个库一旦损坏候补池就没了，落一份文本副本便于人工恢复。
        """
        month_dir = self.archive_dir / subdir / report_date.strftime("%Y-%m") if subdir \
            else self.archive_dir / report_date.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        path = month_dir / f"{report_date.strftime('%Y-%m-%d')}-{project.repo.slug}.md"
        path.write_text(self.build_markdown(project, report_date), encoding="utf-8")
        LOGGER.info("Markdown 归档已生成: %s", path)
        return path
