"""端到端流程测试 —— 全部走 mock 组件，零网络。"""

from __future__ import annotations

from datetime import date

import pytest

import main
from config import Settings
from db import Database
from main import build_components, run_once

REPORT_DATE = date(2026, 8, 12)


@pytest.fixture
def mock_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        report_base_url="http://nas.local:8080/reports",
        mock_mode=True,
        candidate_count=5,
    )


class TestFullRun:
    def test_produces_report_archive_and_push(self, mock_settings):
        s = run_once(mock_settings, report_date=REPORT_DATE)

        assert s.ok is True
        assert s.fetched == 5
        assert s.candidates == 5
        assert s.winner is not None
        assert s.pushed is True

        report = mock_settings.reports_dir / f"2026-08-12-{s.winner.repo.slug}.html"
        assert report.exists()
        assert s.report_path == str(report)
        assert s.report_url.startswith("http://nas.local:8080/reports/")

        archive = mock_settings.archive_dir / "2026-08" / f"2026-08-12-{s.winner.repo.slug}.md"
        assert archive.exists()

    def test_highest_scorer_wins(self, mock_settings):
        s = run_once(mock_settings, report_date=REPORT_DATE)
        # agentmesh 的 mock 评分最高 (92/88/86/95)
        assert s.winner.repo.full_name == "localstack-ai/agentmesh"
        assert s.winner.total_score == pytest.approx(90.0, abs=0.5)

    def test_all_candidates_persisted(self, mock_settings):
        run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        assert db.count() == 5
        statuses = {r["repo_name"]: r["status"] for r in db.recent(10)}
        assert statuses["localstack-ai/agentmesh"] == "pushed"
        assert statuses["quietlabs/ragfoundry"] == "skipped"

    def test_degraded_candidate_is_handled(self, mock_settings):
        """broken-json/llm-router 的 mock 回复不是 JSON → 应降级但不崩。"""
        run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        rows = {r["repo_name"]: r for r in db.recent(10)}
        assert "broken-json/llm-router" in rows
        assert rows["broken-json/llm-router"]["total_score"] < 60

    def test_out_of_range_scores_clamped(self, mock_settings):
        """edge-cases/promptforge 的 mock 评分越界 → 应被夹紧在 0-100。"""
        run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        rows = {r["repo_name"]: r for r in db.recent(10)}
        record = rows["edge-cases/promptforge"]
        assert 0 <= record["total_score"] <= 100
        assert record["rating"] == 5  # 9 被夹到 5
        assert record["difficulty"] == "medium"  # "MEDIUM-HIGH" 回退

    def test_winner_has_complete_tldr(self, mock_settings):
        s = run_once(mock_settings, report_date=REPORT_DATE)
        t = s.winner.analysis.tldr
        assert t.pain and t.solution and t.fit
        assert len(t.pain) <= 60

    def test_partial_tldr_candidate_falls_back(self, mock_settings):
        """promptforge 的假数据只给了 pain —— solution 回退到 description，fit 留空而非报错。"""
        from ai_analyzer import AIAnalyzer
        from mock_data import MOCK_REPOS, MockLLMClient

        repo = next(r for r in MOCK_REPOS if r.full_name == "edge-cases/promptforge")
        analysis = AIAnalyzer(MockLLMClient()).analyze(repo)

        assert analysis.tldr.pain == "同一个 prompt 三个版本散落各处，改坏了不知道是哪次改的"
        assert analysis.tldr.solution == repo.description == "Prompt versioning and eval harness for teams."
        assert analysis.tldr.fit == ""


class TestDedup:
    def test_second_run_skips_pushed_winner(self, mock_settings):
        first = run_once(mock_settings, report_date=REPORT_DATE)
        second = run_once(mock_settings, report_date=REPORT_DATE)

        assert second.candidates == 4
        assert second.winner.repo.full_name != first.winner.repo.full_name

    def test_exhausting_all_candidates_yields_empty_result(self, mock_settings):
        winners = set()
        for _ in range(5):
            s = run_once(mock_settings, report_date=REPORT_DATE)
            if s.winner:
                winners.add(s.winner.repo.full_name)
        final = run_once(mock_settings, report_date=REPORT_DATE)
        assert len(winners) == 5
        assert final.ok is True
        assert final.winner is None
        assert "无候选" in final.reason


class TestEmptyStrategy:
    def test_silent_by_default(self, mock_settings):
        for _ in range(5):
            run_once(mock_settings, report_date=REPORT_DATE)
        s = run_once(mock_settings, report_date=REPORT_DATE)
        assert s.pushed is False

    def test_notify_empty_pushes(self, mock_settings):
        for _ in range(5):
            run_once(mock_settings, report_date=REPORT_DATE)
        mock_settings = mock_settings.model_copy(update={"notify_empty": True})
        s = run_once(mock_settings, report_date=REPORT_DATE)
        assert s.pushed is True


class TestDryRun:
    def test_generates_but_does_not_push(self, mock_settings):
        s = run_once(mock_settings.model_copy(update={"dry_run": True}), report_date=REPORT_DATE)
        assert s.pushed is False
        assert s.report_path
        assert s.winner.status == "skipped"

    def test_dry_run_keeps_candidate_eligible(self, mock_settings):
        dry = mock_settings.model_copy(update={"dry_run": True})
        first = run_once(dry, report_date=REPORT_DATE)
        second = run_once(dry, report_date=REPORT_DATE)
        assert second.winner.repo.full_name == first.winner.repo.full_name


class TestFailureModes:
    def test_github_failure_is_reported_not_raised(self, mock_settings, monkeypatch):
        from github_client import GitHubError

        comps = build_components(mock_settings)

        def boom(**_):
            raise GitHubError("速率限制")

        monkeypatch.setattr(comps["github"], "search_repos", boom)
        s = run_once(mock_settings, report_date=REPORT_DATE, components=comps)
        assert s.ok is False
        assert "GitHub 抓取失败" in s.reason

    def test_push_failure_marks_failed_but_keeps_report(self, mock_settings):
        from wechat_notifier import PushResult

        comps = build_components(mock_settings)
        comps["notifier"].push_project = lambda *_a, **_k: PushResult(ok=False, message="504")

        s = run_once(mock_settings, report_date=REPORT_DATE, components=comps)
        assert s.pushed is False
        assert s.winner.status == "failed"
        assert (mock_settings.reports_dir).exists()
        assert list(mock_settings.reports_dir.glob("*.html"))

    def test_no_webhook_leaves_candidate_eligible(self, tmp_path):
        """未配置 webhook 时标记 skipped，配好后第二天还能重推。"""
        s = Settings(data_dir=tmp_path / "d", mock_mode=False, wechat_webhook_url="")
        comps = build_components(s)
        from mock_data import MockGitHubClient, MockLLMClient
        from ai_analyzer import AIAnalyzer

        comps["github"] = MockGitHubClient()
        comps["analyzer"] = AIAnalyzer(MockLLMClient())

        result = run_once(s, report_date=REPORT_DATE, components=comps)
        assert result.pushed is False
        assert result.winner.status == "skipped"
        assert Database(s.db_path).is_already_pushed(result.winner.repo.full_name) is False


class TestCLI:
    def test_show_config_exits_zero(self, capsys, tmp_path):
        assert main.main(["--show-config", "--data-dir", str(tmp_path)]) == 0
        assert "candidate_count" in capsys.readouterr().out

    def test_mock_uses_separate_data_dir(self):
        args = main.build_parser().parse_args(["--now", "--mock"])
        assert main.settings_from_args(args).data_dir.name == "data-local"

    def test_cli_overrides_win(self):
        args = main.build_parser().parse_args(["--now", "--candidates", "9", "--model", "x-1"])
        s = main.settings_from_args(args)
        assert s.candidate_count == 9
        assert s.llm_model == "x-1"

    def test_now_mock_runs_end_to_end(self, tmp_path):
        code = main.main(["--now", "--mock", "--data-dir", str(tmp_path / "d")])
        assert code == 0
        assert list((tmp_path / "d" / "reports").glob("*.html"))

    def test_list_on_empty_db(self, capsys, tmp_path):
        assert main.main(["--list", "--data-dir", str(tmp_path)]) == 0
        assert "数据库为空" in capsys.readouterr().out


class TestStartupLlmCheck:
    """常驻启动自检 —— 无论如何都不能阻止容器起来。"""

    def test_skipped_in_mock_mode(self, mock_settings, caplog):
        main._startup_llm_check(mock_settings)
        assert "启动自检" not in caplog.text

    def test_warns_when_key_missing(self, tmp_path, caplog):
        s = Settings(data_dir=tmp_path, mock_mode=False, llm_api_key="")
        main._startup_llm_check(s)
        assert "LLM_API_KEY 未配置" in caplog.text

    def test_can_be_disabled(self, tmp_path, caplog):
        s = Settings(data_dir=tmp_path, mock_mode=False,
                     llm_api_key="sk-x", startup_llm_check=False)
        main._startup_llm_check(s)
        assert caplog.text == ""

    def test_llm_failure_is_logged_not_raised(self, tmp_path, caplog, monkeypatch):
        from ai_analyzer import FatalLLMError

        s = Settings(data_dir=tmp_path, mock_mode=False, llm_api_key="sk-bad")
        monkeypatch.setattr(
            "main.LLMClient.complete",
            lambda *_a, **_k: (_ for _ in ()).throw(FatalLLMError("401 Key 无效")),
        )
        main._startup_llm_check(s)  # 不抛异常即为通过
        assert "启动自检失败" in caplog.text
        assert "401" in caplog.text

    def test_non_json_reply_warns(self, tmp_path, caplog, monkeypatch):
        s = Settings(data_dir=tmp_path, mock_mode=False, llm_api_key="sk-x")
        monkeypatch.setattr("main.LLMClient.complete", lambda *_a, **_k: "我不会输出 JSON")
        main._startup_llm_check(s)
        assert "未返回可解析 JSON" in caplog.text

    def test_success_logs_pass(self, tmp_path, caplog, monkeypatch):
        import logging

        s = Settings(data_dir=tmp_path, mock_mode=False, llm_api_key="sk-x")
        monkeypatch.setattr("main.LLMClient.complete", lambda *_a, **_k: '{"ok": true}')
        with caplog.at_level(logging.INFO, logger="main"):
            main._startup_llm_check(s)
        assert "启动自检通过" in caplog.text
