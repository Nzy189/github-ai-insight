"""GitHub Search API 抓取 + README 获取 + 速率限制感知。

PRD §3.1 / §6.2
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from typing import Any, Iterable, Sequence

import requests

from models import Repo

LOGGER = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
SEARCH_TOPICS = ("ai", "llm")


@dataclass(frozen=True, slots=True)
class SearchChannel:
    """一条抓取通道 = 一组（时间窗口, 星数下限）。

    GitHub Search 只能按 `created` 过滤，没有「最近涨星快」这种条件。
    单靠一条窄窗口通道，两个月前创建、如今上万星的项目永远不会出现在候选里 ——
    它早就滑出了 created 窗口。用两条互补的通道逼近：

      新生：窗口窄、门槛低 —— 抓刚出生的
      新星：窗口宽、门槛高 —— 抓已经被市场验证、但我们错过了的
    """

    name: str
    days: int
    min_stars: int
USER_AGENT = "github-ai-insight/1.0"
RETRY_BACKOFF = (5, 15)  # 秒，指数退避


class GitHubError(RuntimeError):
    """GitHub 抓取失败且已耗尽重试。"""


def _interleave(per_channel: Sequence[Sequence[Repo]], limit: int) -> list[Repo]:
    """轮流从每条通道取一个，跨通道去重，取满 limit 为止。"""
    merged: dict[str, Repo] = {}
    for row in zip_longest(*per_channel):
        for repo in row:
            if repo is None or not repo.full_name or repo.full_name in merged:
                continue
            merged[repo.full_name] = repo
            if len(merged) >= limit:
                return list(merged.values())
    return list(merged.values())


class GitHubClient:
    def __init__(
        self,
        token: str = "",
        *,
        session: requests.Session | None = None,
        timeout: int = 30,
        max_rate_limit_wait: int = 300,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.max_rate_limit_wait = max_rate_limit_wait
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ HTTP

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, url: str, *, params: dict[str, Any] | None = None,
             accept: str = "application/vnd.github+json") -> requests.Response:
        """带重试与速率限制感知的 GET。"""
        last_error: Exception | None = None

        for attempt in range(len(RETRY_BACKOFF) + 1):
            try:
                resp = self.session.get(
                    url, params=params, headers=self._headers(accept), timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                LOGGER.warning("GitHub 网络错误 (%s/%s): %s", attempt + 1, len(RETRY_BACKOFF) + 1, exc)
            else:
                if resp.status_code == 401:
                    LOGGER.warning("GitHub 认证失败 (401) — 请检查 GITHUB_TOKEN，本次改用匿名请求")
                    self.token = ""
                    last_error = GitHubError("GitHub token 无效")
                elif resp.status_code == 403 and self._is_rate_limited(resp):
                    waited = self._wait_for_reset(resp)
                    if not waited:
                        raise GitHubError("GitHub 速率限制，重置时间过长，跳过本次")
                    last_error = GitHubError("速率限制")
                elif resp.status_code >= 500:
                    last_error = GitHubError(f"GitHub 服务端错误 {resp.status_code}")
                    LOGGER.warning("%s", last_error)
                elif not resp.ok:
                    raise GitHubError(f"GitHub 请求失败 {resp.status_code}: {resp.text[:200]}")
                else:
                    return resp

            if attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])

        raise GitHubError(f"GitHub 请求重试耗尽: {last_error}")

    @staticmethod
    def _is_rate_limited(resp: requests.Response) -> bool:
        return resp.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in resp.text.lower()

    def _wait_for_reset(self, resp: requests.Response) -> bool:
        """按 X-RateLimit-Reset 等待。返回 False 表示等待时间超过上限，应放弃。"""
        reset = resp.headers.get("X-RateLimit-Reset")
        if not reset:
            LOGGER.warning("速率限制但无 Reset 头，等待 60s")
            time.sleep(60)
            return True
        wait = int(reset) - int(time.time()) + 2
        if wait <= 0:
            return True
        if wait > self.max_rate_limit_wait:
            LOGGER.error("GitHub 速率限制需等待 %ss，超过上限 %ss", wait, self.max_rate_limit_wait)
            return False
        LOGGER.warning("GitHub 速率限制，等待 %ss 后重试", wait)
        time.sleep(wait)
        return True

    # ------------------------------------------------------------------ 搜索

    def search_repos(
        self,
        *,
        days: int = 3,
        limit: int = 5,
        min_stars: int = 10,
        topics: Iterable[str] = SEARCH_TOPICS,
        now: datetime | None = None,
    ) -> list[Repo]:
        """搜索近 N 天创建、带指定 topic 的仓库，按 star 降序取 Top N。

        GitHub Search 不支持 topic 的 OR 语法，因此对每个 topic 分别查询后合并去重。
        """
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).strftime("%Y-%m-%d")

        merged: dict[str, Repo] = {}
        attempted = 0
        failed = 0
        last_error: Exception | None = None

        for topic in topics:
            attempted += 1
            query = f"topic:{topic} created:>={since} stars:>={min_stars}"
            LOGGER.info("GitHub 搜索: %s", query)
            try:
                resp = self._get(
                    f"{API_ROOT}/search/repositories",
                    params={
                        "q": query,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": max(limit * 2, 10),
                    },
                )
            except GitHubError as exc:
                LOGGER.error("topic:%s 搜索失败，跳过该 topic: %s", topic, exc)
                failed += 1
                last_error = exc
                continue

            items = resp.json().get("items", [])
            LOGGER.info("topic:%s 命中 %d 个仓库", topic, len(items))
            for item in items:
                repo = Repo.from_api(item)
                if repo.full_name and repo.full_name not in merged:
                    merged[repo.full_name] = repo

        # 部分 topic 失败可以容忍；全部失败必须上报，
        # 否则"抓取故障"会被下游误判成"今日无新项目"而静默跳过。
        if attempted and failed == attempted:
            raise GitHubError(f"全部 {attempted} 个 topic 搜索均失败，最后错误: {last_error}")

        ranked = sorted(merged.values(), key=lambda r: r.stars, reverse=True)
        LOGGER.info("合并去重后 %d 个仓库，取前 %d 个", len(ranked), limit)
        return ranked[:limit]

    def search_channels(
        self,
        *,
        channels: Sequence[SearchChannel],
        limit: int = 5,
        topics: Iterable[str] = SEARCH_TOPICS,
        now: datetime | None = None,
    ) -> list[Repo]:
        """按多条通道抓取并交替取样。

        刻意【不】把各通道结果合并后按 star 排序：新生通道里的项目普遍只有几十星，
        排序后会被新星通道里那些上万星的巨头永久压在后面，于是「发现新项目」
        这个核心功能会静悄悄退化成「补看旧项目」。
        """
        per_channel: list[list[Repo]] = []
        failed = 0
        last_error: Exception | None = None

        for channel in channels:
            LOGGER.info("通道「%s」: 近 %d 天 / ≥%d 星", channel.name, channel.days, channel.min_stars)
            try:
                repos = self.search_repos(
                    days=channel.days, limit=limit, min_stars=channel.min_stars,
                    topics=topics, now=now,
                )
            except GitHubError as exc:
                LOGGER.error("通道「%s」抓取失败，跳过: %s", channel.name, exc)
                failed += 1
                last_error = exc
                continue
            LOGGER.info("通道「%s」得到 %d 个仓库", channel.name, len(repos))
            per_channel.append(repos)

        # 与 topic 同理：部分通道失败可以容忍，全部失败必须上报。
        if channels and failed == len(channels):
            raise GitHubError(f"全部 {len(channels)} 个通道抓取均失败，最后错误: {last_error}")

        return _interleave(per_channel, limit)

    # ------------------------------------------------------------------ README

    def fetch_readme(self, full_name: str, *, max_chars: int = 24_000) -> str:
        """获取 README 原文并截断。失败返回空串（不阻断流程）。"""
        try:
            resp = self._get(
                f"{API_ROOT}/repos/{full_name}/readme",
                accept="application/vnd.github.raw",
            )
        except GitHubError as exc:
            LOGGER.warning("README 获取失败 %s: %s", full_name, exc)
            return ""

        text = resp.text or ""
        if len(text) > max_chars:
            LOGGER.debug("README 截断 %s: %d -> %d 字符", full_name, len(text), max_chars)
            text = text[:max_chars] + "\n\n...(README 已截断)"
        return text

    def repo_exists(self, full_name: str) -> bool:
        """确认仓库还在（没被删除或改名）。

        候补池里的项目可能是几周前分析的，推之前花一次请求确认一下，
        免得推出去的链接是 404。任何检查失败都当作"还在"——
        网络抖动不该阻止推送。
        """
        try:
            resp = self.session.get(
                f"{API_ROOT}/repos/{full_name}",
                headers=self._headers(),
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            LOGGER.warning("仓库存活检查失败（按存活处理）%s: %s", full_name, exc)
            return True
        if resp.status_code == 404:
            LOGGER.warning("仓库已不存在: %s", full_name)
            return False
        return True

    def enrich(self, repos: list[Repo], *, max_chars: int = 24_000) -> list[Repo]:
        """为每个仓库填充 README。"""
        for repo in repos:
            repo.readme = self.fetch_readme(repo.full_name, max_chars=max_chars)
        return repos
