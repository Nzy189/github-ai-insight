"""候补池选题 —— 当天新项目都打不过历史某个没推过的项目时，把它顶上来。

规则 A（全局最高）：每天推「今天的候选 ∪ 候补池」里总分最高的那个。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from ai_analyzer import restore_from_backlog
from config import Settings
from db import Database
from main import run_once
from models import Repo, Tldr

REPORT_DATE = date(2026, 8, 12)


def row(name: str, score: float, status: str = "skipped", *, summary: dict | None = None) -> dict:
    payload = summary if summary is not None else {
        "one_liner": f"{name} 的一句话",
        "tldr": {"pain": "痛点", "solution": "方案", "fit": "部署"},
        "highlights": ["亮点 A", "亮点 B"],
        "target_audience": "开发者",
        "difficulty": "low",
        "rating": 4,
        "rating_reason": "理由",
        "detailed_intro": "详细介绍",
        "scores": {"utility": 80, "problem_solving": 80, "popularity": 80, "nas_usability": 80},
    }
    return {
        "repo_name": name,
        "repo_url": f"https://github.com/{name}",
        "description": f"{name} description",
        "language": "Python",
        "topics": '["ai", "llm"]',
        "stars": 500,
        "ai_summary": json.dumps(payload, ensure_ascii=False),
        "one_liner": payload.get("one_liner", ""),
        "difficulty": "low",
        "rating": 4,
        "total_score": score,
        "report_path": "",
        "status": status,
        "error_message": "",
    }


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "backlog.db")


@pytest.fixture
def mock_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        report_base_url="http://nas.local:8080/reports",
        mock_mode=True,
        candidate_count=5,
    )


class TestBestBacklog:
    def test_empty_db(self, db):
        assert db.best_backlog() is None

    def test_picks_highest_unpushed(self, db):
        db.save_project(row("a/low", 60.0))
        db.save_project(row("a/high", 84.0))
        db.save_project(row("a/mid", 72.0))
        assert db.best_backlog()["repo_name"] == "a/high"

    def test_ignores_already_pushed(self, db):
        db.save_project(row("a/pushed", 99.0, "pushed"), mark_pushed=True)
        db.save_project(row("a/degraded", 95.0, "degraded"), mark_pushed=True)
        db.save_project(row("a/waiting", 61.0))
        assert db.best_backlog()["repo_name"] == "a/waiting"

    def test_failed_counts_as_backlog(self, db):
        """推送失败的项目从没送达用户，应留在候补池。"""
        db.save_project(row("a/failed", 88.0, "failed"))
        assert db.best_backlog()["repo_name"] == "a/failed"

    def test_backlog_size(self, db):
        db.save_project(row("a/1", 60.0))
        db.save_project(row("a/2", 70.0))
        db.save_project(row("a/3", 80.0, "pushed"), mark_pushed=True)
        assert db.backlog_size() == 2

    def test_users_scenario(self, db):
        """用户提的场景：第一天 85/84/83 推 85；几天后 72/28/79 都不如 84 → 推 84。"""
        db.save_project(row("d1/a", 85.0, "pushed"), mark_pushed=True)
        db.save_project(row("d1/b", 84.0))
        db.save_project(row("d1/c", 83.0))

        # 几天后的三个新项目，最高才 79
        for name, score in (("d2/a", 72.0), ("d2/b", 28.0), ("d2/c", 79.0)):
            db.save_project(row(name, score))

        assert db.best_backlog()["repo_name"] == "d1/b"  # 84 分

        # 推掉 84 之后，下一个应该是 83
        db.save_project(row("d1/b", 84.0, "pushed"), mark_pushed=True)
        assert db.best_backlog()["repo_name"] == "d1/c"


class TestRestoreFromBacklog:
    def test_roundtrip(self, db):
        restored = restore_from_backlog(row("acme/tool", 84.0))
        assert restored is not None
        assert restored.repo.full_name == "acme/tool"
        assert restored.repo.topics == ["ai", "llm"]
        assert restored.repo.stars == 500
        assert restored.analysis.one_liner == "acme/tool 的一句话"
        assert restored.analysis.tldr.pain == "痛点"
        assert restored.from_backlog is True

    def test_old_record_without_tldr(self):
        """早期记录没有 tldr 字段 —— 必须逐字段兜底而不是崩。"""
        old = row("old/repo", 80.0, summary={
            "one_liner": "老记录",
            "highlights": ["A"],
            "difficulty": "low",
            "rating": 4,
            "detailed_intro": "正文",
            "scores": {"utility": 80, "problem_solving": 80,
                       "popularity": 80, "nas_usability": 80},
        })
        restored = restore_from_backlog(old)
        assert isinstance(restored.analysis.tldr, Tldr)
        assert restored.analysis.tldr.pain == "old/repo description"

    def test_corrupt_ai_summary_does_not_raise(self):
        bad = row("bad/json", 70.0)
        bad["ai_summary"] = "{不是合法 JSON"
        restored = restore_from_backlog(bad)
        assert restored is not None
        assert restored.repo.full_name == "bad/json"

    def test_missing_repo_name_returns_none(self):
        broken = row("x/y", 70.0)
        broken["repo_name"] = ""
        assert restore_from_backlog(broken) is None

    def test_repo_from_db_row_handles_bad_topics(self):
        r = Repo.from_db_row({"repo_name": "a/b", "topics": "not json"})
        assert r.topics == []
        assert r.html_url == "https://github.com/a/b"


class TestPipelineFallback:
    def _seed(self, settings, name: str, score: float) -> None:
        Database(settings.db_path).save_project(row(name, score))

    def test_backlog_wins_when_today_is_weaker(self, mock_settings):
        # mock 数据当日最高是 agentmesh 89.6
        self._seed(mock_settings, "history/gem", 95.0)
        s = run_once(mock_settings, report_date=REPORT_DATE)
        assert s.from_backlog is True
        assert s.winner.repo.full_name == "history/gem"
        assert s.pushed is True

    def test_today_wins_when_stronger(self, mock_settings):
        self._seed(mock_settings, "history/meh", 50.0)
        s = run_once(mock_settings, report_date=REPORT_DATE)
        assert s.from_backlog is False
        assert s.winner.repo.full_name == "localstack-ai/agentmesh"

    def test_backlog_pick_leaves_the_pool(self, mock_settings):
        self._seed(mock_settings, "history/gem", 95.0)
        run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        assert db.is_already_pushed("history/gem") is True
        assert (db.best_backlog() or {}).get("repo_name") != "history/gem"

    def test_only_qualified_losers_enter_the_pool(self, mock_settings):
        """5 个候选推 1 个；剩下 4 个里只有够分数的进候补池，
        低于阈值的淘汰、分析失败的等重试。"""
        run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        statuses = {r["repo_name"]: r["status"] for r in db.recent(10)}
        assert statuses["localstack-ai/agentmesh"] == "pushed"
        assert statuses["quietlabs/ragfoundry"] == "skipped"      # 79.3
        assert statuses["nano-tools/whisperbox"] == "skipped"     # 78.1
        assert statuses["edge-cases/promptforge"] == "rejected"   # 56.0 < 65
        assert statuses["broken-json/llm-router"] == "retry"      # 分析失败
        assert db.backlog_size() == 2

    def test_backlog_used_when_no_candidates_at_all(self, mock_settings):
        """去重后一个候选都没有时，也该去候补池捞 —— 而不是直接静默跳过。

        mock 只有 5 个仓库，跑满 5 轮后候选和候补池会同时清空
        （同一批仓库既是候选也是候补），所以先耗尽、再塞一条候补进去。
        """
        for _ in range(5):
            run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        assert db.best_backlog() is None, "5 轮之后候选与候补池应同时清空"

        self._seed(mock_settings, "history/leftover", 66.0)

        s = run_once(mock_settings, report_date=REPORT_DATE)
        assert s.candidates == 0
        assert s.from_backlog is True
        assert s.winner.repo.full_name == "history/leftover"
        assert s.pushed is True

    def test_silent_only_when_both_empty(self, mock_settings):
        """候选和候补池都空了才回到静默跳过。"""
        for _ in range(10):
            run_once(mock_settings, report_date=REPORT_DATE)
        s = run_once(mock_settings, report_date=REPORT_DATE)
        assert s.winner is None
        assert s.ok is True
        assert "无候选" in s.reason

    def test_dead_repo_is_not_pushed(self, mock_settings, monkeypatch):
        """候补项目的仓库已被删除 → 不推它，回落到当日胜出者。"""
        from main import build_components

        self._seed(mock_settings, "deleted/repo", 99.0)
        comps = build_components(mock_settings)
        monkeypatch.setattr(comps["github"], "repo_exists", lambda name: False)

        s = run_once(mock_settings, report_date=REPORT_DATE, components=comps)
        assert s.from_backlog is False
        assert s.winner.repo.full_name == "localstack-ai/agentmesh"

    def test_backlog_report_carries_the_marker(self, mock_settings):
        self._seed(mock_settings, "history/gem", 95.0)
        s = run_once(mock_settings, report_date=REPORT_DATE)
        html = (mock_settings.reports_dir / f"2026-08-12-{s.winner.repo.slug}.html").read_text(
            encoding="utf-8"
        )
        assert "往期精选" in html

    def test_normal_report_has_no_marker(self, mock_settings):
        s = run_once(mock_settings, report_date=REPORT_DATE)
        html = (mock_settings.reports_dir / f"2026-08-12-{s.winner.repo.slug}.html").read_text(
            encoding="utf-8"
        )
        assert "往期精选" not in html

    def test_only_backlog_members_get_archived(self, mock_settings):
        """被淘汰和待重试的没有保存价值，不该占归档空间。"""
        run_once(mock_settings, report_date=REPORT_DATE)
        backlog_dir = mock_settings.archive_dir / "backlog" / "2026-08"
        names = {f.stem for f in backlog_dir.glob("*.md")}
        assert len(names) == 2
        assert any("ragfoundry" in n for n in names)
        assert not any("promptforge" in n for n in names)


class TestWalMode:
    def test_wal_enabled(self, db):
        with db.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
