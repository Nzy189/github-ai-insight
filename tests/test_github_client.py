from __future__ import annotations

import time

import pytest
import requests

from github_client import GitHubClient, GitHubError, SearchChannel

NEWBORN = SearchChannel("新生", days=3, min_stars=10)
RISING = SearchChannel("新星", days=90, min_stars=500)


class _Resp:
    def __init__(self, *, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self.text = text or "{}"
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        result = self.responses.pop(0) if self.responses else _Resp(payload={"items": []})
        if isinstance(result, Exception):
            raise result
        return result


def _items(*names):
    return {
        "items": [
            {
                "full_name": name,
                "html_url": f"https://github.com/{name}",
                "description": "d",
                "language": "Python",
                "topics": ["ai"],
                "stargazers_count": stars,
                "owner": {"login": name.split("/")[0]},
            }
            for name, stars in names
        ]
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)


class TestHeaders:
    def test_token_sets_authorization(self):
        session = _Session([_Resp(payload=_items(("a/b", 10)))] * 2)
        GitHubClient("tok123", session=session).search_repos()
        assert session.calls[0]["headers"]["Authorization"] == "Bearer tok123"

    def test_anonymous_has_no_authorization(self):
        session = _Session([_Resp(payload=_items(("a/b", 10)))] * 2)
        GitHubClient("", session=session).search_repos()
        assert "Authorization" not in session.calls[0]["headers"]


class TestSearch:
    def test_query_shape(self):
        session = _Session([_Resp(payload=_items())] * 2)
        GitHubClient(session=session).search_repos(days=3, min_stars=25)
        q = session.calls[0]["params"]["q"]
        assert "topic:ai" in q
        assert "created:>=" in q
        assert "stars:>=25" in q
        assert session.calls[0]["params"]["sort"] == "stars"

    def test_queries_both_topics(self):
        session = _Session([_Resp(payload=_items())] * 2)
        GitHubClient(session=session).search_repos()
        queries = [c["params"]["q"] for c in session.calls]
        assert any("topic:ai" in q for q in queries)
        assert any("topic:llm" in q for q in queries)

    def test_merges_and_dedupes_across_topics(self):
        session = _Session([
            _Resp(payload=_items(("a/one", 100), ("a/two", 50))),
            _Resp(payload=_items(("a/two", 50), ("a/three", 200))),
        ])
        repos = GitHubClient(session=session).search_repos(limit=10)
        assert [r.full_name for r in repos] == ["a/three", "a/one", "a/two"]

    def test_respects_limit(self):
        session = _Session([
            _Resp(payload=_items(*[(f"a/r{i}", 100 - i) for i in range(10)])),
            _Resp(payload=_items()),
        ])
        assert len(GitHubClient(session=session).search_repos(limit=3)) == 3

    def test_one_topic_failing_does_not_kill_the_run(self):
        session = _Session([
            _Resp(status_code=404, text="not found"),
            _Resp(payload=_items(("a/ok", 10))),
        ])
        repos = GitHubClient(session=session).search_repos()
        assert [r.full_name for r in repos] == ["a/ok"]


class TestSearchChannels:
    """多通道抓取 —— created:>= 只认创建时间，单通道必然漏掉「两个月前建、现在爆火」的项目。"""

    def test_each_channel_uses_its_own_window_and_star_floor(self):
        session = _Session([_Resp(payload=_items())] * 4)
        GitHubClient(session=session).search_channels(channels=[NEWBORN, RISING])
        queries = [c["params"]["q"] for c in session.calls]
        assert sum("stars:>=10" in q for q in queries) == 2   # 两个 topic
        assert sum("stars:>=500" in q for q in queries) == 2

    def test_interleaves_so_the_high_star_channel_cannot_starve_the_newborn_one(self):
        """按 star 合并排序的话，90 天的巨头会把 3 天的新项目永久挤出候选 ——
        那就等于把「发现新项目」这个核心功能换成了「补看旧项目」。
        """
        session = _Session([
            _Resp(payload=_items(("new/a", 40), ("new/b", 30))), _Resp(payload=_items()),
            _Resp(payload=_items(("old/x", 9000), ("old/y", 8000))), _Resp(payload=_items()),
        ])
        repos = GitHubClient(session=session).search_channels(
            channels=[NEWBORN, RISING], limit=4)
        assert [r.full_name for r in repos] == ["new/a", "old/x", "new/b", "old/y"]

    def test_dedupes_across_channels(self):
        """3 天内创建又已经过 500 星的项目会同时命中两条通道。"""
        session = _Session([
            _Resp(payload=_items(("both/x", 900))), _Resp(payload=_items()),
            _Resp(payload=_items(("both/x", 900), ("old/y", 800))), _Resp(payload=_items()),
        ])
        repos = GitHubClient(session=session).search_channels(
            channels=[NEWBORN, RISING], limit=10)
        assert [r.full_name for r in repos] == ["both/x", "old/y"]

    def test_one_channel_failing_does_not_kill_the_run(self):
        session = _Session(
            [_Resp(status_code=503)] * 6
            + [_Resp(payload=_items(("old/x", 900))), _Resp(payload=_items())]
        )
        repos = GitHubClient(session=session).search_channels(channels=[NEWBORN, RISING])
        assert [r.full_name for r in repos] == ["old/x"]

    def test_all_channels_failing_raises_not_returns_empty(self):
        session = _Session([_Resp(status_code=503)] * 12)
        with pytest.raises(GitHubError, match="全部 2 个通道"):
            GitHubClient(session=session).search_channels(channels=[NEWBORN, RISING])

    def test_empty_channel_list_is_not_an_error(self):
        assert GitHubClient(session=_Session([])).search_channels(channels=[]) == []


class TestRetryAndRateLimit:
    def test_retries_on_network_error(self):
        session = _Session([
            requests.ConnectionError("boom"),
            _Resp(payload=_items(("a/b", 5))),
            _Resp(payload=_items()),
        ])
        repos = GitHubClient(session=session).search_repos()
        assert [r.full_name for r in repos] == ["a/b"]

    def test_retries_on_5xx(self):
        session = _Session([
            _Resp(status_code=503, text="oops"),
            _Resp(payload=_items(("a/b", 5))),
            _Resp(payload=_items()),
        ])
        assert len(GitHubClient(session=session).search_repos()) == 1

    def test_all_topics_failing_raises_not_returns_empty(self):
        """全部失败必须抛异常 —— 否则会被下游误判成"今日无新项目"而静默跳过。"""
        session = _Session([_Resp(status_code=503)] * 6)
        with pytest.raises(GitHubError, match="全部 2 个 topic"):
            GitHubClient(session=session).search_repos()

    def test_partial_failure_is_tolerated(self):
        session = _Session([
            _Resp(status_code=503), _Resp(status_code=503), _Resp(status_code=503),
            _Resp(payload=_items(("a/ok", 10))),
        ])
        assert [r.full_name for r in GitHubClient(session=session).search_repos()] == ["a/ok"]

    def test_genuinely_empty_result_is_not_an_error(self):
        """搜索成功但没有结果 → 正常返回空列表。"""
        session = _Session([_Resp(payload=_items()), _Resp(payload=_items())])
        assert GitHubClient(session=session).search_repos() == []

    def test_rate_limit_waits_for_reset(self):
        reset = int(time.time()) + 5
        session = _Session([
            _Resp(status_code=403, text="API rate limit exceeded",
                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}),
            _Resp(payload=_items(("a/b", 5))),
            _Resp(payload=_items()),
        ])
        assert len(GitHubClient(session=session).search_repos()) == 1

    def test_rate_limit_too_long_aborts_topic(self):
        reset = int(time.time()) + 99999
        session = _Session([
            _Resp(status_code=403, text="API rate limit exceeded",
                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}),
            _Resp(payload=_items(("a/b", 5))),
        ])
        # 第一个 topic 放弃，第二个 topic 正常
        assert len(GitHubClient(session=session).search_repos()) == 1

    def test_401_falls_back_to_anonymous(self):
        session = _Session([
            _Resp(status_code=401, text="bad creds"),
            _Resp(payload=_items(("a/b", 5))),
            _Resp(payload=_items()),
        ])
        client = GitHubClient("bad-token", session=session)
        client.search_repos()
        assert client.token == ""
        assert "Authorization" not in session.calls[1]["headers"]


class TestReadme:
    def test_truncates(self):
        session = _Session([_Resp(text="x" * 5000)])
        readme = GitHubClient(session=session).fetch_readme("a/b", max_chars=100)
        assert len(readme) < 200
        assert "已截断" in readme

    def test_short_readme_untouched(self):
        session = _Session([_Resp(text="# Hello")])
        assert GitHubClient(session=session).fetch_readme("a/b") == "# Hello"

    def test_failure_returns_empty_string(self):
        session = _Session([_Resp(status_code=404, text="no readme")])
        assert GitHubClient(session=session).fetch_readme("a/b") == ""

    def test_requests_raw_accept_header(self):
        session = _Session([_Resp(text="# R")])
        GitHubClient(session=session).fetch_readme("a/b")
        assert session.calls[0]["headers"]["Accept"] == "application/vnd.github.raw"


def test_error_type_is_exported():
    assert issubclass(GitHubError, RuntimeError)
