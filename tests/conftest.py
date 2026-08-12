from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试无需安装即可 import 项目模块
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from models import Analysis, AnalyzedProject, Repo, Scores  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """阻断真实 .env 与环境变量，保证测试结果稳定。"""
    for key in (
        "GITHUB_TOKEN", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "WECHAT_WEBHOOK_URL", "DATA_DIR", "EXECUTION_TIME", "TIMEZONE",
        "CANDIDATE_COUNT", "SEARCH_DAYS", "NOTIFY_EMPTY", "LOG_LEVEL",
        "REPORT_BASE_URL", "HTTP_PORT", "LLM_PROVIDER", "MIN_STARS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        report_base_url="http://nas.local:8080/reports",
        timezone="Asia/Dubai",
        mock_mode=True,
    )


@pytest.fixture
def repo() -> Repo:
    return Repo(
        full_name="acme/cool-agent",
        html_url="https://github.com/acme/cool-agent",
        description="An agent framework",
        language="Python",
        topics=["ai", "llm"],
        stars=1234,
        forks=56,
        open_issues=7,
        created_at="2026-08-10T08:00:00Z",
        owner="acme",
    )


@pytest.fixture
def analysis() -> Analysis:
    return Analysis(
        one_liner="把多智能体编排搬回自己的机器",
        highlights=["亮点一", "亮点二", "亮点三"],
        target_audience="自托管开发者",
        difficulty="medium",
        rating=4,
        rating_reason="部署简单",
        detailed_intro="## 标题\n\n这是**详细介绍**，带 `代码` 和 [链接](https://example.com)。",
        scores=Scores(utility=90, problem_solving=80, popularity=70, nas_usability=60),
    )


@pytest.fixture
def project(repo, analysis) -> AnalyzedProject:
    return AnalyzedProject(repo=repo, analysis=analysis)
