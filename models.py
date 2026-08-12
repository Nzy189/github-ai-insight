"""领域数据模型 — 各模块之间传递的统一结构。

刻意用 dataclass 而非 pydantic：这些对象只在进程内流转，
校验发生在边界（config / ai_analyzer 的 JSON 解析）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from config import SCORE_WEIGHTS

Difficulty = Literal["low", "medium", "high"]
Status = Literal["pushed", "failed", "degraded", "skipped"]


@dataclass(slots=True)
class Repo:
    """GitHub Search API 返回的仓库元数据。"""

    full_name: str
    html_url: str
    description: str = ""
    language: str = ""
    topics: list[str] = field(default_factory=list)
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    created_at: str = ""
    pushed_at: str = ""
    owner: str = ""
    readme: str = ""

    @property
    def slug(self) -> str:
        """文件名安全的仓库标识: owner_repo。"""
        return self.full_name.replace("/", "_")

    @property
    def repo_name(self) -> str:
        return self.full_name.split("/")[-1]

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "Repo":
        """从 projects 表的一行还原 Repo —— 候补池取用时走这条路。

        只还原推送与渲染需要的字段；fork/issue 数当时没存，留默认值。
        """
        try:
            topics = json.loads(row.get("topics") or "[]")
        except (json.JSONDecodeError, TypeError):
            topics = []
        full_name = row.get("repo_name") or ""
        return cls(
            full_name=full_name,
            html_url=row.get("repo_url") or f"https://github.com/{full_name}",
            description=row.get("description") or "",
            language=row.get("language") or "",
            topics=topics if isinstance(topics, list) else [],
            stars=int(row.get("stars") or 0),
            owner=full_name.split("/")[0] if "/" in full_name else "",
        )

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Repo":
        owner = (item.get("owner") or {}).get("login", "")
        return cls(
            full_name=item.get("full_name", ""),
            html_url=item.get("html_url", ""),
            description=item.get("description") or "",
            language=item.get("language") or "",
            topics=list(item.get("topics") or []),
            stars=int(item.get("stargazers_count") or 0),
            forks=int(item.get("forks_count") or 0),
            open_issues=int(item.get("open_issues_count") or 0),
            created_at=item.get("created_at") or "",
            pushed_at=item.get("pushed_at") or "",
            owner=owner,
        )


@dataclass(slots=True)
class Scores:
    utility: int = 50
    problem_solving: int = 50
    popularity: int = 50
    nas_usability: int = 50

    def as_dict(self) -> dict[str, int]:
        return {
            "utility": self.utility,
            "problem_solving": self.problem_solving,
            "popularity": self.popularity,
            "nas_usability": self.nas_usability,
        }

    @property
    def total(self) -> float:
        """加权总分（PRD §3.2），保留 1 位小数。"""
        raw = sum(getattr(self, k) * w for k, w in SCORE_WEIGHTS.items())
        return round(raw, 1)


@dataclass(slots=True)
class Tldr:
    """首屏三要素 —— 痛点 / 怎么解决 / 我能用吗。

    任一字段允许为空：模板会整行隐藏，而不是渲染出一个空壳。
    """

    pain: str = ""
    solution: str = ""
    fit: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"pain": self.pain, "solution": self.solution, "fit": self.fit}

    @property
    def is_empty(self) -> bool:
        return not (self.pain or self.solution or self.fit)


@dataclass(slots=True)
class Analysis:
    """LLM 结构化输出。"""

    one_liner: str = ""
    highlights: list[str] = field(default_factory=list)
    target_audience: str = ""
    difficulty: Difficulty = "medium"
    rating: int = 3
    rating_reason: str = ""
    detailed_intro: str = ""
    scores: Scores = field(default_factory=Scores)
    tldr: Tldr = field(default_factory=Tldr)
    degraded: bool = False
    degrade_reason: str = ""
    raw_json: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "one_liner": self.one_liner,
            "tldr": self.tldr.as_dict(),
            "highlights": self.highlights,
            "target_audience": self.target_audience,
            "difficulty": self.difficulty,
            "rating": self.rating,
            "rating_reason": self.rating_reason,
            "detailed_intro": self.detailed_intro,
            "scores": self.scores.as_dict(),
        }


@dataclass(slots=True)
class AnalyzedProject:
    """一个候选项目的完整分析结果 — 报告生成与推送的唯一输入。"""

    repo: Repo
    analysis: Analysis
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    report_path: str = ""
    report_url: str = ""
    status: Status = "skipped"
    error_message: str = ""
    # 取自候补池（往期分析过但没推送过），而非当天新抓取的
    from_backlog: bool = False
    backlog_analyzed_at: str = ""

    @property
    def total_score(self) -> float:
        return self.analysis.scores.total

    def to_db_row(self) -> dict[str, Any]:
        return {
            "repo_name": self.repo.full_name,
            "repo_url": self.repo.html_url,
            "description": self.repo.description,
            "language": self.repo.language,
            "topics": json.dumps(self.repo.topics, ensure_ascii=False),
            "stars": self.repo.stars,
            "ai_summary": self.analysis.raw_json or json.dumps(
                self.analysis.as_dict(), ensure_ascii=False
            ),
            "one_liner": self.analysis.one_liner,
            "difficulty": self.analysis.difficulty,
            "rating": self.analysis.rating,
            "total_score": self.total_score,
            "report_path": self.report_path,
            "status": self.status,
            "error_message": self.error_message,
        }
