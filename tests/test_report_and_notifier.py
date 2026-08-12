from __future__ import annotations

from datetime import date

import pytest

from models import Analysis, Scores, Tldr
from report_generator import ReportGenerator, render_markdown, score_tier
from wechat_notifier import WeChatNotifier, build_empty_markdown, build_markdown

REPORT_DATE = date(2026, 8, 12)


@pytest.fixture
def generator(tmp_path) -> ReportGenerator:
    return ReportGenerator(
        tmp_path / "reports",
        tmp_path / "archive",
        report_base_url="http://nas.local:8080/reports",
        model_name="gpt-4o",
    )


class TestScoreTier:
    @pytest.mark.parametrize("score,tier", [
        (100, "high"), (80, "high"), (79.9, "mid"), (50, "mid"), (49.9, "low"), (0, "low"),
    ])
    def test_boundaries(self, score, tier):
        assert score_tier(score) == tier


class TestRenderMarkdown:
    def test_basic_markdown(self):
        html = render_markdown("**粗体** 和 `代码`")
        assert "<strong>粗体</strong>" in html
        assert "<code>代码</code>" in html

    def test_escapes_raw_html(self):
        html = render_markdown('<script>alert(1)</script>')
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_empty(self):
        assert render_markdown("") == ""
        assert render_markdown("   ") == ""

    def test_lists_render(self):
        assert "<li>" in render_markdown("- 一\n- 二")


class TestHtmlReport:
    def test_file_naming(self, generator, project):
        path, url = generator.write_report(project, REPORT_DATE)
        assert path.name == "2026-08-12-acme_cool-agent.html"
        assert url == "http://nas.local:8080/reports/2026-08-12-acme_cool-agent.html"
        assert project.report_path == str(path)
        assert project.report_url == url

    def test_is_self_contained(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "<style>" in html
        assert "https://fonts.googleapis" not in html
        assert "cdn." not in html
        assert "<script" not in html

    def test_contains_key_content(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert project.repo.repo_name in html
        assert project.repo.html_url in html
        assert project.analysis.one_liner in html
        for h in project.analysis.highlights:
            assert h in html
        assert "2026-08-12" in html

    def test_score_bars_and_ring(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        # 90 → high, 60 → mid
        assert "score-high" in html
        assert "score-mid" in html
        assert "width: 90%" in html
        assert str(int(round(project.total_score))) in html

    def test_responsive_and_print_styles(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "@media (max-width: 640px)" in html
        assert "@media print" in html
        assert "prefers-reduced-motion" in html

    def test_degraded_banner_only_when_degraded(self, generator, project):
        assert "本报告为降级数据" not in generator.render_html(project, REPORT_DATE)
        project.analysis.degraded = True
        project.analysis.degrade_reason = "LLM 超时"
        html = generator.render_html(project, REPORT_DATE)
        assert "本报告为降级数据" in html
        assert "LLM 超时" in html

    def test_autoescape_blocks_injection(self, generator, project):
        project.analysis.one_liner = '<img src=x onerror="alert(1)">'
        html = generator.render_html(project, REPORT_DATE)
        assert 'onerror="alert(1)"' not in html
        assert "&lt;img" in html

    def test_written_file_is_valid_utf8(self, generator, project):
        path, _ = generator.write_report(project, REPORT_DATE)
        content = path.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "把多智能体编排搬回自己的机器" in content


class TestArchive:
    def test_path_layout(self, generator, project):
        path = generator.write_archive(project, REPORT_DATE)
        assert path.parent.name == "2026-08"
        assert path.name == "2026-08-12-acme_cool-agent.md"

    def test_content(self, generator, project):
        md = generator.build_markdown(project, REPORT_DATE)
        assert f"# {project.repo.full_name}" in md
        assert project.analysis.one_liner in md
        assert "| 实用性 | 35% | 90 |" in md
        assert "★★★★☆" in md

    def test_degraded_note(self, generator, project):
        project.analysis.degraded = True
        project.analysis.degrade_reason = "配额不足"
        assert "配额不足" in generator.build_markdown(project, REPORT_DATE)


class TestWeChatMarkdown:
    def test_structure(self, project):
        project.report_url = "http://nas.local:8080/reports/x.html"
        msg = build_markdown(project, REPORT_DATE)
        assert "GitHub AI 日报" in msg
        assert "2026-08-12" in msg
        assert f"[{project.repo.full_name}]({project.repo.html_url})" in msg
        assert "推荐指数**: 4/5" in msg
        assert project.analysis.one_liner in msg
        assert "查看完整分析报告" in msg
        assert project.report_url in msg

    def test_only_three_highlights(self, project):
        project.analysis.highlights = [f"亮点{i}" for i in range(10)]
        msg = build_markdown(project, REPORT_DATE)
        assert "亮点0" in msg and "亮点2" in msg
        assert "亮点3" not in msg

    def test_degraded_warning(self, project):
        project.analysis.degraded = True
        project.analysis.degrade_reason = "LLM 超时"
        assert "AI 分析降级" in build_markdown(project, REPORT_DATE)

    def test_no_report_link_when_missing(self, project):
        project.report_url = ""
        assert "查看完整分析报告" not in build_markdown(project, REPORT_DATE)

    def test_byte_limit(self, project):
        project.analysis.one_liner = "长" * 5000
        msg = build_markdown(project, REPORT_DATE)
        assert len(msg.encode("utf-8")) <= 4096

    def test_empty_message(self):
        assert "今日无新 AI 项目发现" in build_empty_markdown(REPORT_DATE)


class _StubResponse:
    def __init__(self, ok=True, status_code=200, payload=None, text=""):
        self.ok, self.status_code = ok, status_code
        self._payload = payload if payload is not None else {"errcode": 0, "errmsg": "ok"}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class _StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json, timeout):  # noqa: A002
        self.calls.append(json)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TestNotifier:
    def test_skips_when_not_configured(self):
        result = WeChatNotifier("").send_markdown("hi")
        assert result.skipped is True
        assert result.ok is False

    def test_success(self):
        session = _StubSession([_StubResponse()])
        result = WeChatNotifier("http://hook", session=session, retry_delays=()).send_markdown("hi")
        assert result.ok is True
        assert session.calls[0]["msgtype"] == "markdown"
        assert session.calls[0]["markdown"]["content"] == "hi"

    def test_retries_then_succeeds(self):
        session = _StubSession([
            _StubResponse(ok=False, status_code=500),
            _StubResponse(),
        ])
        notifier = WeChatNotifier("http://hook", session=session, retry_delays=(0,))
        assert notifier.send_markdown("hi").ok is True
        assert len(session.calls) == 2

    def test_nonzero_errcode_is_failure(self):
        session = _StubSession([_StubResponse(payload={"errcode": 93000, "errmsg": "invalid"})])
        result = WeChatNotifier("http://hook", session=session, retry_delays=()).send_markdown("hi")
        assert result.ok is False
        assert "93000" in result.message

    def test_exhausts_retries(self):
        session = _StubSession([_StubResponse(ok=False, status_code=500)] * 3)
        notifier = WeChatNotifier("http://hook", session=session, retry_delays=(0, 0))
        assert notifier.send_markdown("hi").ok is False
        assert len(session.calls) == 3


class TestHeroB2:
    def _html(self, generator, project):
        return generator.render_html(project, REPORT_DATE)

    def test_one_liner_is_the_h1(self, generator, project):
        html = self._html(generator, project)
        h1 = html.split("<h1")[1].split("</h1>")[0]
        assert project.analysis.one_liner in h1
        assert project.repo.repo_name not in h1

    def test_repo_name_demoted_to_subtitle(self, generator, project):
        html = self._html(generator, project)
        assert project.repo.full_name in html
        assert "hero-sub" in html

    def test_tldr_three_rows_rendered(self, generator, project):
        project.analysis.tldr = Tldr(pain="痛点内容", solution="方案内容", fit="部署内容")
        html = self._html(generator, project)
        for text in ("痛点内容", "方案内容", "部署内容"):
            assert text in html
        assert html.count('class="tldr-row"') == 3

    def test_empty_tldr_row_is_hidden(self, generator, project):
        project.analysis.tldr = Tldr(pain="只有痛点", solution="", fit="")
        html = self._html(generator, project)
        assert html.count('class="tldr-row"') == 1
        assert "只有痛点" in html

    def test_tldr_section_absent_when_all_empty(self, generator, project):
        project.analysis.tldr = Tldr()
        html = self._html(generator, project)
        assert 'class="tldr-row"' not in html

    def test_tldr_labels_present(self, generator, project):
        project.analysis.tldr = Tldr(pain="a", solution="b", fit="c")
        html = self._html(generator, project)
        for label in ("痛点", "怎么解决", "我能用吗"):
            assert label in html

    def test_verdict_bar_carries_rating_and_difficulty(self, generator, project):
        html = self._html(generator, project)
        assert "verdict-bar" in html
        assert project.analysis.rating_reason in html
        assert "入门友好" in html or "需要折腾" in html or "硬核" in html

    def test_hero_verdict_reason_has_its_own_singly_defined_class(self, generator, project):
        """回归测试：Hero 的 .verdict-reason 曾与快速上手区块同名选择器撞车，
        后者在样式表里位置更靠后，按级联规则会覆盖 Hero 自己的 13px/次要色样式。
        Hero 必须使用专属类名 hero-verdict-reason，且该选择器只能定义一次。
        """
        html = self._html(generator, project)
        assert 'class="hero-verdict-reason"' in html
        assert html.count(".hero-verdict-reason") == 1

    def test_ring_geometry_matches_52px(self, generator, project):
        html = self._html(generator, project)
        assert 'viewBox="0 0 52 52"' in html
        assert 'cx="26"' in html

    def test_tldr_is_escaped(self, generator, project):
        project.analysis.tldr = Tldr(pain='<img src=x onerror="alert(1)">')
        html = self._html(generator, project)
        assert 'onerror="alert(1)"' not in html
        assert "&lt;img" in html

    def test_label_font_size_meets_nano_floor(self, generator, project):
        """DESIGN.md Nano 下限 11px —— 不得出现 9px/10px 字号。"""
        html = self._html(generator, project)
        assert "font-size: 9px" not in html
        assert "font-size: 10px" not in html
