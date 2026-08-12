from __future__ import annotations

import json

import pytest

from ai_analyzer import (
    AIAnalyzer,
    FatalLLMError,
    LLMError,
    build_degraded_analysis,
    extract_json,
    heuristic_popularity,
    normalize_analysis,
    normalize_tldr,
    pick_winner,
)
from models import Analysis, AnalyzedProject, Repo, Scores, Tldr

GOOD = {
    "one_liner": "一句话",
    "highlights": ["A", "B"],
    "target_audience": "开发者",
    "difficulty": "low",
    "rating": 5,
    "rating_reason": "好用",
    "detailed_intro": "详细介绍",
    "scores": {"utility": 90, "problem_solving": 80, "popularity": 70, "nas_usability": 60},
}


class TestExtractJson:
    def test_plain(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_without_lang(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_surrounded_by_prose(self):
        assert extract_json('好的，结果如下：\n{"a": 1}\n希望有帮助') == {"a": 1}

    def test_nested_object(self):
        text = json.dumps({"scores": {"utility": 1}}, ensure_ascii=False)
        assert extract_json(f"前言 {text} 后记")["scores"]["utility"] == 1

    @pytest.mark.parametrize("bad", ["", "   ", "完全不是 JSON", "[1,2,3]"])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            extract_json(bad)


class TestNormalize:
    def test_happy_path(self, repo):
        a = normalize_analysis(GOOD, repo)
        assert a.one_liner == "一句话"
        assert a.difficulty == "low"
        assert a.rating == 5
        assert a.scores.total == pytest.approx(79.0)
        assert a.degraded is False

    @pytest.mark.parametrize("raw,expected", [(9, 5), (0, 1), (-3, 1), ("4", 4), (None, 3), ("x", 3)])
    def test_rating_clamped(self, repo, raw, expected):
        assert normalize_analysis({**GOOD, "rating": raw}, repo).rating == expected

    @pytest.mark.parametrize("raw,expected", [(150, 100), (-20, 0), (55.6, 56), ("77", 77)])
    def test_scores_clamped(self, repo, raw, expected):
        a = normalize_analysis({**GOOD, "scores": {**GOOD["scores"], "utility": raw}}, repo)
        assert a.scores.utility == expected

    @pytest.mark.parametrize("raw", ["MEDIUM-HIGH", "简单", "", None, 3])
    def test_bad_difficulty_falls_back(self, repo, raw):
        assert normalize_analysis({**GOOD, "difficulty": raw}, repo).difficulty == "medium"

    def test_difficulty_case_insensitive(self, repo):
        assert normalize_analysis({**GOOD, "difficulty": "HIGH"}, repo).difficulty == "high"

    def test_highlights_from_string(self, repo):
        a = normalize_analysis({**GOOD, "highlights": "- A\n- B\n- C"}, repo)
        assert a.highlights == ["A", "B", "C"]

    def test_highlights_capped(self, repo):
        a = normalize_analysis({**GOOD, "highlights": [f"h{i}" for i in range(20)]}, repo)
        assert len(a.highlights) == 6

    def test_missing_scores_defaults(self, repo):
        a = normalize_analysis({k: v for k, v in GOOD.items() if k != "scores"}, repo)
        assert a.scores.utility == 50
        assert a.scores.popularity == heuristic_popularity(repo.stars)

    def test_empty_one_liner_falls_back_to_description(self, repo):
        a = normalize_analysis({**GOOD, "one_liner": ""}, repo)
        assert a.one_liner == repo.description

    def test_raw_json_preserved(self, repo):
        a = normalize_analysis(GOOD, repo)
        assert json.loads(a.raw_json)["one_liner"] == "一句话"


class TestDegraded:
    def test_uses_github_description(self, repo):
        a = build_degraded_analysis(repo, "API Key 无效")
        assert a.degraded is True
        assert a.one_liner == repo.description
        assert "API Key 无效" in a.degrade_reason
        assert a.scores.utility == 50
        assert a.scores.problem_solving == 50
        assert a.scores.nas_usability == 50

    def test_handles_empty_description(self):
        repo = Repo(full_name="a/b", html_url="u", description="")
        a = build_degraded_analysis(repo, "reason")
        assert a.one_liner

    @pytest.mark.parametrize("stars,low,high", [(0, 40, 50), (500, 60, 70), (50000, 90, 100)])
    def test_popularity_heuristic_monotonic(self, stars, low, high):
        assert low <= heuristic_popularity(stars) <= high


class _StubLLM:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def complete(self, system, user):
        if self.error:
            raise self.error
        return self.reply


class TestAnalyzer:
    def test_no_client_degrades(self, repo):
        a = AIAnalyzer(None).analyze(repo)
        assert a.degraded is True

    def test_good_reply(self, repo):
        a = AIAnalyzer(_StubLLM(json.dumps(GOOD))).analyze(repo)
        assert a.degraded is False
        assert a.rating == 5

    def test_unparsable_reply_degrades(self, repo):
        a = AIAnalyzer(_StubLLM("这不是 JSON")).analyze(repo)
        assert a.degraded is True
        assert "非 JSON" in a.degrade_reason

    def test_fatal_error_degrades(self, repo):
        a = AIAnalyzer(_StubLLM(error=FatalLLMError("Key 无效"))).analyze(repo)
        assert a.degraded is True

    def test_retryable_error_degrades(self, repo):
        a = AIAnalyzer(_StubLLM(error=LLMError("超时"))).analyze(repo)
        assert a.degraded is True

    def test_prompt_contains_repo_facts(self, repo):
        prompt = AIAnalyzer(None).build_prompt(repo)
        assert repo.full_name in prompt
        assert str(repo.stars) in prompt

    def test_analyze_all_returns_projects(self, repo):
        results = AIAnalyzer(_StubLLM(json.dumps(GOOD))).analyze_all([repo, repo])
        assert len(results) == 2
        assert all(isinstance(p, AnalyzedProject) for p in results)


class TestPickWinner:
    def _p(self, name, total_scores, stars=0):
        return AnalyzedProject(
            repo=Repo(full_name=name, html_url="u", stars=stars),
            analysis=Analysis(scores=Scores(*total_scores)),
        )

    def test_empty(self):
        assert pick_winner([]) is None

    def test_highest_total_wins(self):
        low = self._p("a/low", (10, 10, 10, 10))
        high = self._p("a/high", (90, 90, 90, 90))
        assert pick_winner([low, high]).repo.full_name == "a/high"

    def test_tie_broken_by_stars(self):
        a = self._p("a/few", (80, 80, 80, 80), stars=10)
        b = self._p("a/many", (80, 80, 80, 80), stars=9999)
        assert pick_winner([a, b]).repo.full_name == "a/many"


FULL_TLDR = {
    "pain": "平台已经能检测出你的稿子是 AI 写的",
    "solution": "三层清理：隐形字符、文本水印、元数据",
    "fit": "纯 Python 标准库，不用 Docker",
}


class TestNormalizeTldr:
    def test_full_payload(self, repo):
        t = normalize_tldr({"tldr": FULL_TLDR}, repo)
        assert t.pain == FULL_TLDR["pain"]
        assert t.solution == FULL_TLDR["solution"]
        assert t.fit == FULL_TLDR["fit"]

    def test_missing_key_entirely(self, repo):
        """整个 tldr 缺失 → pain 取 description，solution 留空避免重复。"""
        t = normalize_tldr({}, repo)
        assert t.pain == repo.description
        assert t.solution == ""

    @pytest.mark.parametrize("bad", ["一段字符串", 42, None, ["a", "b"]])
    def test_wrong_type_does_not_raise(self, repo, bad):
        t = normalize_tldr({"tldr": bad}, repo)
        assert isinstance(t, Tldr)

    def test_partial_only_pain(self, repo):
        t = normalize_tldr({"tldr": {"pain": "只有痛点"}}, repo)
        assert t.pain == "只有痛点"
        # pain 没消耗 description，solution 可以回退到它
        assert t.solution == repo.description
        assert t.fit == ""

    def test_solution_not_duplicated_with_pain(self, repo):
        """pain 已回退到 description 时，solution 不得再印同一句。"""
        t = normalize_tldr({"tldr": {"fit": "有 Docker"}}, repo)
        assert t.pain == repo.description
        assert t.solution == ""

    def test_fit_inferred_from_docker_topic(self):
        r = Repo(full_name="a/b", html_url="u", description="d", topics=["ai", "Docker"])
        assert normalize_tldr({}, r).fit == "仓库标注了 Docker 支持"

    def test_fit_left_empty_without_docker_topic(self, repo):
        assert normalize_tldr({}, repo).fit == ""

    def test_normalize_analysis_populates_tldr(self, repo):
        a = normalize_analysis({**GOOD, "tldr": FULL_TLDR}, repo)
        assert a.tldr.pain == FULL_TLDR["pain"]

    def test_old_payload_without_tldr_still_works(self, repo):
        """数据库里的旧记录没有 tldr 键 —— 不能抛异常。"""
        a = normalize_analysis(GOOD, repo)
        assert isinstance(a.tldr, Tldr)


class TestDegradedTldr:
    def test_degraded_fills_all_three(self, repo):
        a = build_degraded_analysis(repo, "LLM 超时")
        assert a.tldr.pain == repo.description
        assert repo.language in a.tldr.solution
        assert "README" in a.tldr.fit

    def test_degraded_fit_does_not_fabricate(self, repo):
        """降级时不许编造部署结论。"""
        fit = build_degraded_analysis(repo, "x").tldr.fit
        assert "AI 分析不可用" in fit
        assert "Docker" not in fit

    def test_degraded_pain_handles_empty_description(self):
        r = Repo(full_name="a/b", html_url="u", description="")
        assert build_degraded_analysis(r, "x").tldr.pain == "该仓库未填写描述"


class TestPromptHardConstraints:
    def _prompt(self, repo):
        return AIAnalyzer(None).build_prompt(repo)

    def test_declares_tldr_in_json_schema(self, repo):
        p = self._prompt(repo)
        assert '"tldr"' in p
        assert '"pain"' in p
        assert '"solution"' in p
        assert '"fit"' in p

    def test_has_one_liner_writing_section(self, repo):
        assert "一句话总结（one_liner）的写法" in self._prompt(repo)

    def test_has_tldr_writing_section(self, repo):
        assert "首屏三要素（tldr）的写法" in self._prompt(repo)

    def test_shows_negative_example_verbatim(self, repo):
        """反面示例必须原样给出 —— 抽象地说'不要名词堆叠'模型听不懂。"""
        p = self._prompt(repo)
        assert "多供应商AI水印移除工具" in p
        assert "名词短语堆叠" in p

    def test_shows_positive_example(self, repo):
        assert "AI 写的东西会被偷偷打上隐形标记" in self._prompt(repo)

    def test_forbids_empty_fit_phrasing(self, repo):
        assert "适合自托管用户" in self._prompt(repo)

    def test_prompt_still_formats_without_keyerror(self, repo):
        """新增小节里若混入未转义的花括号，.format() 会炸。"""
        p = self._prompt(repo)
        assert repo.full_name in p
        assert str(repo.stars) in p
