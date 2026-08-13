"""LLM 调用 + 结构化 JSON 解析 + 加权打分。

PRD §3.2 / §3.3 / §6.1
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from models import Analysis, AnalyzedProject, Repo, Scores, Tldr

LOGGER = logging.getLogger(__name__)

RETRY_BACKOFF = (5, 15)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_VALID_DIFFICULTY = {"low", "medium", "high"}

SYSTEM_PROMPT = """你是一位资深的开源技术分析师，专门为自托管 NAS 用户评估 GitHub 上的 AI/LLM 开源项目。
你的输出必须是**严格的 JSON**，不要包含任何 JSON 之外的解释文字，不要使用 Markdown 代码块包裹。"""

USER_PROMPT_TEMPLATE = """请分析以下 GitHub 项目，并按指定 JSON 格式输出评估结果。

## 项目元数据
- 仓库名: {full_name}
- 链接: {html_url}
- 描述: {description}
- 主语言: {language}
- Topics: {topics}
- Star 数: {stars}
- Fork 数: {forks}
- 创建时间: {created_at}

## README（可能已截断）
{readme}

## 输出要求

严格返回以下结构的 JSON（所有字段必填）：

{{
  "one_liner": "一句人话总结，完整句子且带使用场景，30 字以内。见下方写法要求",
  "tldr": {{
    "pain": "不用它会怎样，具体场景，40 字以内",
    "solution": "它具体干了什么，讲人话，40 字以内",
    "fit": "部署事实：依赖 / 内存 / 要不要 Docker / 要不要显卡，40 字以内"
  }},
  "highlights": ["核心技术亮点1", "核心技术亮点2", "核心技术亮点3"],
  "target_audience": "适用场景与目标群体描述，60 字以内",
  "difficulty": "low | medium | high 三选一",
  "rating": 1到5的整数,
  "rating_reason": "推荐或不推荐的理由，60 字以内",
  "detailed_intro": "面向普通用户的详细介绍，400-700 字。见下方"详细介绍的写法"，必须分段",
  "scores": {{
    "utility": 0到100的整数,
    "problem_solving": 0到100的整数,
    "popularity": 0到100的整数,
    "nas_usability": 0到100的整数
  }}
}}

## 评分标准
- utility（实用性）: 是否解决实际工程问题、功能完整度
- problem_solving（解决问题能力）: 痛点明确性、方案可行性
- popularity（受欢迎程度）: Star 数与近期增长趋势，结合项目年龄判断
- nas_usability（NAS 可用性）: 是否提供 Docker 镜像/compose、资源占用是否适合家用 NAS（无独显、内存有限）

## 一句话总结（one_liner）的写法

**硬性要求：必须是完整句子，必须带使用场景，30 字以内。**
读者读完这一句就要能复述"这东西是干嘛的"。

反面示例（禁止这样写）：
"多供应商AI水印移除工具，清理文本与文件元数据以保护隐私"
这是名词短语堆叠 —— 读者读完仍不知道自己什么时候会用到它。

正面示例：
"AI 写的东西会被偷偷打上隐形标记 —— 这工具帮你洗干净"
有场景、有动作、是完整句子。

## 首屏三要素（tldr）的写法

三句各 40 字以内，缺一不可：

- pain —— 不用它会怎样。必须是具体场景，不许写抽象名词
- solution —— 它具体干了什么。讲人话，不要罗列 API 名或依赖名
- fit —— 部署事实。必须包含以下至少一项：语言与运行时依赖、
  内存或存储占用、是否提供 Docker、是否需要显卡。
  禁止写"适合自托管用户"这类没有信息量的话

## 详细介绍的写法

**硬性要求：必须分成 3 到 5 个自然段，段落之间用两个换行符分隔。**
在 JSON 字符串里写成 `\\n\\n`。整段不分段的输出视为不合格。

建议的段落结构：
1. 开头先讲痛点场景 —— 读者在什么情况下会需要它
2. 它是怎么解决的，关键技术点讲人话（不要罗列 API）
3. 实际部署与上手情况 —— 资源占用、依赖、折腾程度，该泼冷水就泼
4. 结尾一句明确判断 —— 值不值得试，什么人应该点进去看

风格要求：口语化，像给朋友安利一个好用的工具，不是念 README。
可以用 `**加粗**` 强调关键结论，每段最多一处。不要使用任何标题语法。

全部用简体中文输出。只输出 JSON。"""


class LLMError(RuntimeError):
    """LLM 调用失败（已耗尽重试）。"""


class FatalLLMError(LLMError):
    """不可重试的失败：Key 无效、余额不足。"""


# ---------------------------------------------------------------- 降级评分


def heuristic_popularity(stars: int) -> int:
    """无 LLM 时按 star 数粗略估算受欢迎程度。"""
    if stars >= 10_000:
        return 95
    if stars >= 3_000:
        return 85
    if stars >= 1_000:
        return 75
    if stars >= 300:
        return 65
    if stars >= 100:
        return 55
    return 45


def build_degraded_analysis(repo: Repo, reason: str) -> Analysis:
    """PRD §6.1: 用 GitHub description 兜底，评分置 50（受欢迎度按 star 估算）。"""
    one_liner = repo.description.strip() or f"{repo.repo_name} — GitHub 上的 AI 开源项目"
    highlights = [f"主语言: {repo.language or '未知'}", f"Star 数: {repo.stars}"]
    if repo.topics:
        highlights.append("Topics: " + ", ".join(repo.topics[:5]))

    return Analysis(
        one_liner=one_liner,
        highlights=highlights,
        target_audience="AI / LLM 方向的开发者与自托管爱好者",
        difficulty="medium",
        rating=3,
        rating_reason="AI 分析不可用，本条为 GitHub 元数据兜底摘要，建议自行查看仓库。",
        detailed_intro=(
            f"本次未能完成 AI 深度分析（原因：{reason}）。\n\n"
            f"以下为 GitHub 原始信息：\n\n"
            f"**{repo.full_name}** 目前有 {repo.stars} 个 Star、{repo.forks} 个 Fork，"
            f"主要使用 {repo.language or '未标注语言'} 开发。\n\n"
            f"官方描述：{repo.description or '（该仓库未填写描述）'}"
        ),
        scores=Scores(
            utility=50,
            problem_solving=50,
            popularity=heuristic_popularity(repo.stars),
            nas_usability=50,
        ),
        tldr=Tldr(
            pain=repo.description.strip() or "该仓库未填写描述",
            # solution 标签是"怎么解决"，塞仓库元数据答非所问；
            # 同样的内容已在 highlights 与仓库信息表里，留空让模板隐藏该行。
            solution="",
            fit="AI 分析不可用，部署要求请查看仓库 README",
        ),
        degraded=True,
        degrade_reason=reason,
    )


# ---------------------------------------------------------------- JSON 解析


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 回复中抠出 JSON 对象。容忍代码块包裹与前后废话。"""
    if not text or not text.strip():
        raise ValueError("LLM 返回空内容")

    candidates: list[str] = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"无法从 LLM 回复中解析出 JSON: {text[:200]!r}")


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


TLDR_MAX_CHARS = 80


def _truncate(text: str, limit: int = TLDR_MAX_CHARS) -> str:
    """超长首屏文案硬截断。

    Prompt 要求 40 字，这里放宽到 80 —— 不是对文风的第二意见，
    只是防止某一次话痨输出把整个首屏撑爆。
    """
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def normalize_tldr(payload: dict[str, Any], repo: Repo) -> Tldr:
    """解析首屏三要素，逐字段兜底。

    模型漏一个字段不该毁掉整页，所以这里不整体降级。
    solution 只在 pain 没有消耗掉 description 时才回退到它 ——
    否则首屏会把同一句话印两遍。
    三个字段最终都会被截断到 TLDR_MAX_CHARS。
    """
    raw = payload.get("tldr")
    if not isinstance(raw, dict):
        raw = {}

    description = repo.description.strip()

    pain = _as_str(raw.get("pain")) or description
    used_description_for_pain = bool(description) and pain == description

    solution = _as_str(raw.get("solution"))
    if not solution and not used_description_for_pain:
        solution = description

    fit = _as_str(raw.get("fit"))
    if not fit and any(t.lower() == "docker" for t in repo.topics):
        fit = "仓库标注了 Docker 支持"

    return Tldr(pain=_truncate(pain), solution=_truncate(solution), fit=_truncate(fit))


def normalize_analysis(payload: dict[str, Any], repo: Repo) -> Analysis:
    """把 LLM 的 JSON 规整成 Analysis，越界值一律夹紧而不是报错。"""
    raw_highlights = payload.get("highlights")
    if isinstance(raw_highlights, str):
        highlights = [h.strip(" -•\t") for h in raw_highlights.splitlines() if h.strip()]
    elif isinstance(raw_highlights, list):
        highlights = [_as_str(h) for h in raw_highlights if _as_str(h)]
    else:
        highlights = []

    difficulty = _as_str(payload.get("difficulty"), "medium").lower()
    if difficulty not in _VALID_DIFFICULTY:
        LOGGER.warning("difficulty 非法 %r，回退 medium", payload.get("difficulty"))
        difficulty = "medium"

    raw_scores = payload.get("scores")
    if not isinstance(raw_scores, dict):
        raw_scores = {}
    scores = Scores(
        utility=_clamp_int(raw_scores.get("utility"), 0, 100, 50),
        problem_solving=_clamp_int(raw_scores.get("problem_solving"), 0, 100, 50),
        popularity=_clamp_int(
            raw_scores.get("popularity"), 0, 100, heuristic_popularity(repo.stars)
        ),
        nas_usability=_clamp_int(raw_scores.get("nas_usability"), 0, 100, 50),
    )

    return Analysis(
        one_liner=_as_str(payload.get("one_liner")) or repo.description or repo.repo_name,
        highlights=highlights[:6],
        target_audience=_as_str(payload.get("target_audience")),
        difficulty=difficulty,  # type: ignore[arg-type]
        rating=_clamp_int(payload.get("rating"), 1, 5, 3),
        rating_reason=_as_str(payload.get("rating_reason")),
        detailed_intro=_as_str(payload.get("detailed_intro")) or _as_str(payload.get("one_liner")),
        scores=scores,
        tldr=normalize_tldr(payload, repo),
        raw_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )


# ---------------------------------------------------------------- LLM 客户端


class LLMClient:
    """OpenAI / Anthropic 兼容的最小客户端。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai",
        timeout: int = 120,
        max_tokens: int = 4096,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.session = session or requests.Session()

    def _request_payload(self, system: str, user: str) -> tuple[str, dict, dict]:
        if self.provider == "anthropic":
            url = f"{self.base_url}/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            body = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        else:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": 0.4,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            }
        return url, headers, body

    @staticmethod
    def _extract_text(provider: str, data: dict[str, Any]) -> str:
        if provider == "anthropic":
            blocks = data.get("content") or []
            return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content") or ""

    def complete(self, system: str, user: str) -> str:
        """调用 LLM，返回纯文本回复。失败时按 PRD §6.1 重试 2 次（5s / 15s）。"""
        if not self.api_key:
            raise FatalLLMError("LLM_API_KEY 未配置")

        url, headers, body = self._request_payload(system, user)
        last_error: Exception | None = None

        for attempt in range(len(RETRY_BACKOFF) + 1):
            try:
                resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                LOGGER.warning("LLM 网络错误 (%d/%d): %s", attempt + 1, len(RETRY_BACKOFF) + 1, exc)
            else:
                if resp.status_code == 401:
                    raise FatalLLMError("LLM API Key 无效 (401)")
                if resp.status_code in (402, 429):
                    LOGGER.error("⚠️ LLM 配额或余额问题 (%s): %s", resp.status_code, resp.text[:200])
                    raise FatalLLMError(f"LLM 配额不足或限流 ({resp.status_code})")
                if resp.ok:
                    text = self._extract_text(self.provider, resp.json())
                    if text.strip():
                        return text
                    last_error = LLMError("LLM 返回空内容")
                else:
                    last_error = LLMError(f"LLM 返回 {resp.status_code}: {resp.text[:200]}")
                    LOGGER.warning("%s", last_error)

            if attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])

        raise LLMError(f"LLM 调用重试耗尽: {last_error}")


# ---------------------------------------------------------------- 分析编排


class AIAnalyzer:
    def __init__(self, client: LLMClient | None) -> None:
        self.client = client

    def build_prompt(self, repo: Repo) -> str:
        return USER_PROMPT_TEMPLATE.format(
            full_name=repo.full_name,
            html_url=repo.html_url,
            description=repo.description or "（无描述）",
            language=repo.language or "未知",
            topics=", ".join(repo.topics) or "无",
            stars=repo.stars,
            forks=repo.forks,
            created_at=repo.created_at or "未知",
            readme=repo.readme or "（未能获取 README）",
        )

    def analyze(self, repo: Repo) -> Analysis:
        """分析单个仓库。任何失败都降级而非抛出，保证流程不中断。"""
        if self.client is None:
            return build_degraded_analysis(repo, "未配置 LLM 客户端")

        try:
            raw = self.client.complete(SYSTEM_PROMPT, self.build_prompt(repo))
        except FatalLLMError as exc:
            LOGGER.error("LLM 致命错误，全部降级: %s", exc)
            return build_degraded_analysis(repo, str(exc))
        except LLMError as exc:
            LOGGER.error("LLM 调用失败 %s: %s", repo.full_name, exc)
            return build_degraded_analysis(repo, str(exc))

        try:
            payload = extract_json(raw)
        except ValueError as exc:
            LOGGER.error("LLM 输出解析失败 %s: %s", repo.full_name, exc)
            return build_degraded_analysis(repo, "LLM 返回内容非 JSON")

        analysis = normalize_analysis(payload, repo)

        # 质量告警：整段不分段的正文在手机上是一堵字墙，违反 DESIGN.md 的留白原则。
        # 不自动改写（拆句子容易拆坏），只提示 —— 频繁出现就该调 Prompt 或换模型。
        intro = analysis.detailed_intro
        if len(intro) > 300 and "\n\n" not in intro:
            LOGGER.warning(
                "detailed_intro 未分段（%d 字，%s）—— 报告正文会显得拥挤",
                len(intro), repo.full_name,
            )

        LOGGER.info(
            "分析完成 %s | 总分 %.1f | 难度 %s | 推荐 %d/5",
            repo.full_name,
            analysis.scores.total,
            analysis.difficulty,
            analysis.rating,
        )
        return analysis

    @property
    def model_name(self) -> str:
        return getattr(self.client, "model", "") if self.client else ""

    def analyze_all(self, repos: list[Repo]) -> list[AnalyzedProject]:
        return [
            AnalyzedProject(
                repo=repo,
                analysis=self.analyze(repo),
                llm_model=self.model_name,
            )
            for repo in repos
        ]


def restore_from_backlog(row: dict[str, Any]) -> AnalyzedProject | None:
    """把候补池的一行还原成可直接推送的 AnalyzedProject，不再调用 LLM。

    `ai_summary` 存的是当初 LLM 的原始 JSON（降级记录存的是 `as_dict()` 的产物），
    两种形态键名一致，都能交给 `normalize_analysis` 重新规整 —— 包括早期
    没有 `tldr` 字段的老记录，会走逐字段兜底而不是抛异常。
    """
    repo = Repo.from_db_row(row)
    if not repo.full_name:
        LOGGER.warning("候补记录缺少 repo_name，跳过")
        return None

    try:
        payload = json.loads(row.get("ai_summary") or "{}")
    except (json.JSONDecodeError, TypeError):
        LOGGER.warning("候补记录 %s 的 ai_summary 不是合法 JSON，按空处理", repo.full_name)
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    return AnalyzedProject(
        repo=repo,
        analysis=normalize_analysis(payload, repo),
        from_backlog=True,
        backlog_analyzed_at=str(row.get("fetched_at") or "")[:10],
        # 保留当初给出这份分析的模型，不要冒充成现在配置的那个
        llm_model=str(row.get("llm_model") or ""),
    )


def pick_winner(projects: list[AnalyzedProject]) -> AnalyzedProject | None:
    """PRD §3.2: 选出加权总分最高的 1 个。同分时 star 多者胜。"""
    if not projects:
        return None
    return max(projects, key=lambda p: (p.total_score, p.repo.stars))
