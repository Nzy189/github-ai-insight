from __future__ import annotations

import re
from datetime import date

import pytest

from models import Analysis, Scores, Tldr
from report_generator import ReportGenerator, render_markdown, score_tier
from wechat_notifier import WeChatNotifier, build_empty_markdown, build_markdown

REPORT_DATE = date(2026, 8, 12)

# 任意标签上的 on* 事件属性。只匹配 `<` 与 `>` 之间的内容，
# 所以正文里作为纯文本出现的 `onclick=` 不会误报。
EVENT_HANDLER_ATTR = re.compile(r"<[a-zA-Z][^>]*\son[a-z]+\s*=")


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

    def test_attr_list_cannot_decorate_inline_tag(self):
        """python-markdown 的 extra 合集含 attr_list，`{: ... }` 能给标签塞任意属性，
        而 html.escape 只处理 <>&，花括号原样通过 —— 转义拦不住它。
        扩展白名单必须排除 attr_list。"""
        html = render_markdown('这是**加粗**{: onmouseover="alert(1)" }')
        assert "<strong>加粗</strong>" in html
        assert not EVENT_HANDLER_ATTR.search(html), html
        assert "<strong onmouseover" not in html

    def test_attr_list_cannot_decorate_heading(self):
        html = render_markdown('## 标题 {: id=x onclick="steal()" }')
        assert "<h2>" in html
        assert not EVENT_HANDLER_ATTR.search(html), html
        assert 'id="x"' not in html

    def test_ordinary_rendering_survives_extension_whitelist(self):
        """去掉 extra 之后，日常语法不能跟着一起消失。"""
        assert "<strong>粗" in render_markdown("**粗**")
        assert "<h2>标题</h2>" in render_markdown("## 标题")
        assert "<li>一</li>" in render_markdown("- 一\n- 二")
        assert "<code>x</code>" in render_markdown("`x`")
        fenced = render_markdown("```python\nprint(1)\n```")
        assert "<pre>" in fenced and "print(1)" in fenced
        table = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in table and "<td>1</td>" in table


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

    def test_attr_list_payload_in_intro_cannot_inject_handler(self, generator, project):
        """detailed_intro 是第三方 README 派生的模型文本 —— 被投毒的 README
        不得通过 attr_list 语法在报告页上挂事件处理器。"""
        project.analysis.detailed_intro = (
            '这是**加粗**{: onmouseover="alert(1)" }\n\n'
            '## 标题 {: id=pwn onclick="steal()" }'
        )
        html = generator.render_html(project, REPORT_DATE)
        assert not EVENT_HANDLER_ATTR.search(html), "报告页出现了事件处理器属性"
        assert 'id="pwn"' not in html

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
        assert project.analysis.one_liner, "fixture 的 one_liner 不能为空，否则断言恒真"
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
        # fixture 的 difficulty 是 medium —— 必须断言对应的那一个标签，
        # "任意一个都行" 对每种难度都恒真，抓不到映射错误。
        assert project.analysis.difficulty == "medium"
        assert "需要折腾" in html
        assert "入门友好" not in html
        assert "硬核" not in html

    def test_rating_is_readable_as_text_not_only_glyphs(self, generator, project):
        """WCAG 1.4.1：五个相同的 ★ 字符对读屏用户等于没有信息。
        星形必须带 aria-label，且页面上要有可见的 N/5 文本。"""
        html = self._html(generator, project)
        rating = project.analysis.rating
        assert f'aria-label="推荐指数 {rating}/5"' in html
        assert 'class="verdict-stars" role="img"' in html
        assert f'class="verdict-rating" aria-hidden="true">{rating}/5<' in html

    def test_difficulty_badge_has_accessible_name(self, generator, project):
        html = self._html(generator, project)
        assert 'aria-label="上手难度 需要折腾"' in html

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
        """DESIGN.md Nano 下限 11px。

        旧版只 grep 字面量 'font-size: 9px' / 'font-size: 10px'，
        对 'font-size:10px'（无空格）、'0.625rem'、以及任何新增的小字号都无效。
        这里改为扫描整页每一条 font-size 声明并取最小值。
        """
        html = self._html(generator, project)
        declarations = re.findall(r"font-size\s*:\s*([^;}\"']+)", html)
        assert declarations, "页面里一条 font-size 都没有，扫描逻辑失效"

        # 先确保没有 rem/em —— 否则下面的 px 扫描不能算完整覆盖。
        relative = [d.strip() for d in declarations if re.search(r"\d\s*(rem|em)\b", d)]
        assert not relative, f"font-size 不得使用 rem/em 单位（px 扫描会漏掉）: {relative}"

        pixel_values = [float(m) for m in re.findall(r"font-size\s*:\s*([\d.]+)px", html)]
        assert len(pixel_values) == len(declarations), (
            f"存在非 px 的 font-size 声明: "
            f"{[d.strip() for d in declarations if not re.match(r'^[\\d.]+px$', d.strip())]}"
        )
        smallest = min(pixel_values)
        assert smallest >= 11, f"字号 {smallest}px 低于 DESIGN.md 的 Nano 下限 11px"


class TestSectionOrder:
    SECTIONS = ["技术亮点", "详细介绍", "评分依据", "仓库信息"]

    def test_sections_appear_in_reading_order(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        positions = [html.index(f'id="sec-{k}"') for k in
                     ("highlights", "intro", "scores", "meta")]
        assert positions == sorted(positions), "区块顺序必须是 亮点→正文→评分→仓库"

    def test_score_section_titled_评分依据(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "评分依据" in html
        assert "评分详情" not in html

    def test_scores_use_compact_rows_not_grid(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert html.count('class="score-line"') == 4
        assert "score-grid" not in html
        assert "score-card" not in html

    def test_score_lines_carry_weight_and_value(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        for token in ("35%", "30%", "25%", "10%"):
            assert token in html
        assert "width: 90%" in html  # utility=90 in the fixture

    def test_target_audience_moved_into_highlights(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        highlights_block = html.split('id="sec-highlights"')[1].split('id="sec-intro"')[0]
        assert project.analysis.target_audience in highlights_block

    def test_old_quickstart_section_removed(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "快速上手" not in html
        assert "verdict-grid" not in html

    def test_duplicate_cta_removed(self, generator, project):
        """CTA 已进 Hero，底部不再重复。"""
        html = generator.render_html(project, REPORT_DATE)
        assert html.count("前往 GitHub 仓库 ↗") == 1

    def test_no_dead_css_left_behind(self, generator, project):
        """失效选择器必须删干净 —— 旧 .verdict-reason 位置靠后会覆盖 Hero 的新定义。"""
        html = generator.render_html(project, REPORT_DATE)
        for selector in (".verdict-grid", ".verdict-item", ".cta-row",
                         ".btn-ghost", ".score-bar", ".score-card",
                         ".badge-rating", "--shadow-lg"):
            assert selector not in html, f"残留失效 CSS: {selector}"

    def test_old_verdict_reason_selector_fully_gone(self, generator, project):
        """Task 3 已把 Hero 的同名规则重命名为 .hero-verdict-reason，
        因此删掉旧的 15px 规则后，.verdict-reason 应当一个都不剩。
        注意 '.hero-verdict-reason' 不含 '.verdict-reason' 子串（点后面接的是 h），
        所以这条断言不会被 Hero 的新类名意外满足。"""
        html = generator.render_html(project, REPORT_DATE)
        assert ".verdict-reason" not in html
        assert html.count(".hero-verdict-reason") == 1
        assert 'class="hero-verdict-reason"' in html


class TestArchiveLink:
    """报告页回归档首页的入口 —— 从企微点开报告后能跳到往期全部推送。"""

    def test_link_present_and_derived_from_base_url(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "查看往期全部推送" in html
        assert 'href="http://nas.local:8080/"' in html

    def test_absent_when_no_base_url(self, tmp_path, project):
        gen = ReportGenerator(tmp_path / "r", tmp_path / "a", report_base_url="", model_name="m")
        html = gen.render_html(project, REPORT_DATE)
        assert "查看往期全部推送" not in html

    def test_uses_absolute_url_not_root_relative(self, generator, project):
        """报告可能被下载后用 file:// 打开，相对的 "/" 会指向文件系统根。"""
        html = generator.render_html(project, REPORT_DATE)
        assert 'class="archive-link" href="/"' not in html

    @pytest.mark.parametrize("base,expected", [
        ("http://n:8080/reports", "http://n:8080/"),
        ("http://n:8080/reports/", "http://n:8080/"),
        ("http://n:8080/x", "http://n:8080/x/"),
    ])
    def test_index_url_derivation(self, tmp_path, base, expected):
        gen = ReportGenerator(tmp_path / "r", tmp_path / "a", report_base_url=base, model_name="m")
        assert gen.archive_index_url == expected
