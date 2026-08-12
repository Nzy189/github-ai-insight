"""本地测试用假数据与假客户端 — 零网络跑通完整链路。

由 `--mock` 启用。刻意混入以下边界情况，让本地测试能覆盖降级分支：
  - repo #3 的 LLM 回复带 ```json 代码块包裹
  - repo #4 的 LLM 回复是非 JSON 文本 → 触发降级
  - repo #5 的评分越界 / difficulty 非法 → 触发夹紧与回退
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from models import Repo

LOGGER = logging.getLogger(__name__)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


MOCK_REPOS: list[Repo] = [
    Repo(
        full_name="localstack-ai/agentmesh",
        html_url="https://github.com/localstack-ai/agentmesh",
        description="Self-hosted multi-agent orchestration runtime with a single-binary deploy.",
        language="Go",
        topics=["ai", "llm", "agents", "self-hosted", "docker"],
        stars=4821,
        forks=312,
        open_issues=47,
        created_at=_iso(2),
        pushed_at=_iso(0),
        owner="localstack-ai",
        readme="# AgentMesh\n\nRun multi-agent LLM workflows on your own hardware...",
    ),
    Repo(
        full_name="quietlabs/ragfoundry",
        html_url="https://github.com/quietlabs/ragfoundry",
        description="Batteries-included RAG pipeline builder with a visual editor.",
        language="Python",
        topics=["ai", "rag", "llm", "vector-database"],
        stars=2140,
        forks=155,
        open_issues=23,
        created_at=_iso(1),
        pushed_at=_iso(0),
        owner="quietlabs",
        readme="# RAGFoundry\n\nDrag-and-drop RAG pipelines, ships with Docker Compose...",
    ),
    Repo(
        full_name="nano-tools/whisperbox",
        html_url="https://github.com/nano-tools/whisperbox",
        description="CPU-only speech-to-text service tuned for low-power NAS boxes.",
        language="Rust",
        topics=["ai", "whisper", "speech-to-text", "self-hosted"],
        stars=1687,
        forks=98,
        open_issues=12,
        created_at=_iso(3),
        pushed_at=_iso(1),
        owner="nano-tools",
        readme="# WhisperBox\n\nTranscribe audio without a GPU. 300MB RAM footprint...",
    ),
    Repo(
        full_name="broken-json/llm-router",
        html_url="https://github.com/broken-json/llm-router",
        description="Lightweight LLM gateway with failover and cost routing.",
        language="TypeScript",
        topics=["ai", "llm", "gateway"],
        stars=932,
        forks=61,
        open_issues=8,
        created_at=_iso(2),
        pushed_at=_iso(0),
        owner="broken-json",
        readme="# llm-router\n\nRoute requests across providers...",
    ),
    Repo(
        full_name="edge-cases/promptforge",
        html_url="https://github.com/edge-cases/promptforge",
        description="Prompt versioning and eval harness for teams.",
        language="Python",
        topics=["ai", "llm", "prompt-engineering", "evaluation"],
        stars=615,
        forks=40,
        open_issues=19,
        created_at=_iso(1),
        pushed_at=_iso(0),
        owner="edge-cases",
        readme="# PromptForge\n\nVersion, diff and evaluate prompts...",
    ),
]


def _payload(**kwargs: Any) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


MOCK_LLM_RESPONSES: dict[str, str] = {
    "localstack-ai/agentmesh": _payload(
        one_liner="把多智能体编排从云平台搬回自己的机器，一个二进制文件就能跑起来",
        tldr={
            "pain": "想串起几个 Agent 协同干活，最后写出一堆胶水代码还难排查",
            "solution": "写一个 YAML 描述任务图，调度、重试、超时全交给运行时",
            "fit": "单个 Go 二进制，镜像不到 40MB，2GB 内存的 NAS 能跑",
        },
        highlights=[
            "单二进制部署，无需 Python 环境或依赖地狱，NAS 上 docker run 一条命令启动",
            "内置任务图编排引擎，支持 Agent 之间的消息传递、重试与超时控制",
            "兼容 OpenAI / Anthropic / Ollama 三套 API，可混合调度本地与云端模型",
        ],
        target_audience="想在自己 NAS 或家庭服务器上跑多智能体工作流，又不愿把数据交给云平台的开发者与自托管爱好者",
        difficulty="medium",
        rating=5,
        rating_reason="部署成本极低、功能完整度高，是目前少见的同时兼顾自托管与多智能体编排的方案。",
        detailed_intro=(
            "如果你折腾过 LLM Agent，大概率被两件事劝退过：一是各家框架的 Python 依赖动辄几百兆，"
            "在 NAS 上装完发现内存不够；二是想串起几个 Agent 协同干活，最后写出一堆胶水代码，出错了还不知道卡在哪一步。\n\n"
            "**AgentMesh 就是冲着这两个痛点来的。**\n\n"
            "它用 Go 写成单个二进制文件，整个镜像不到 40MB，在一台 2GB 内存的入门 NAS 上也能跑得动。"
            "你只需要写一个 YAML 描述任务图——谁先跑、谁依赖谁的输出、失败了重试几次——剩下的调度、"
            "消息传递、超时熔断全部由运行时接管。\n\n"
            "更实用的是它的模型路由。你可以把便宜的活儿丢给本地 Ollama，把需要推理能力的环节交给云端模型，"
            "配置里改一行就能切换，不用动业务逻辑。对于既想控制成本又不想牺牲效果的场景，这个设计相当加分。\n\n"
            "上手门槛不算低——你得能看懂任务图的概念，也需要花点时间理解它的重试语义。"
            "但官方给的 `docker-compose.yml` 是开箱即用的，配上 Web UI 观察每个 Agent 的执行轨迹，"
            "调试体验比裸写脚本舒服太多。\n\n"
            "**一句话：如果你有一台闲置的 NAS，又想认真玩一下多智能体，这个项目值得你今晚就 clone 下来。**"
        ),
        scores={"utility": 92, "problem_solving": 88, "popularity": 86, "nas_usability": 95},
    ),
    "quietlabs/ragfoundry": _payload(
        one_liner="用拖拽的方式搭 RAG 流水线，省掉反复调参写胶水代码的时间",
        tldr={
            "pain": "调 RAG 参数每改一次就要重跑索引，一下午就没了",
            "solution": "把整条流水线做成可视化节点，改完点一下就重跑并出评估对比",
            "fit": "有 Docker Compose，但本地 Embedding 模型内存占用可观",
        },
        highlights=[
            "可视化编辑器，切分策略、Embedding 模型、检索方式都能在界面上直接换",
            "内置 12 种分块策略与 6 种向量库适配器，切换只需改一个下拉框",
            "自带评估面板，能对比不同配置下的召回率与答案质量",
        ],
        target_audience="需要快速验证 RAG 方案的产品团队与独立开发者",
        difficulty="low",
        rating=4,
        rating_reason="降低 RAG 试错成本的效果明显，但深度定制时仍需回到代码层。",
        detailed_intro=(
            "搭一套 RAG 最烦的不是写代码，是**调参**。切多长的块？重叠多少？换个 Embedding 模型效果会不会更好？"
            "每改一次就要重新跑一遍索引，一下午就没了。\n\n"
            "RAGFoundry 把这些环节做成了可视化流水线。你在界面上把「文档加载 → 分块 → 向量化 → 检索 → 重排」连起来，"
            "每个节点点开都能换实现方式，改完点一下就重跑，结果直接在右边的评估面板上对比。\n\n"
            "它内置了 12 种分块策略和 6 种向量库适配器（Qdrant、Milvus、pgvector 都在列），"
            "所以你不需要为了试一个新方案去读一遍新库的文档。\n\n"
            "部署方面提供了 Docker Compose，一条命令拉起服务加向量库，"
            "在 NAS 上跑是可以的，但要注意 Embedding 模型如果选本地的，内存占用会比较可观。\n\n"
            "**适合什么人**：正在做 RAG 原型、需要快速对比几套方案的团队。"
            "如果你的需求已经很确定、只需要一条固定链路，那直接写代码可能更省事。"
        ),
        scores={"utility": 84, "problem_solving": 82, "popularity": 74, "nas_usability": 68},
    ),
    # 带 Markdown 代码块包裹 —— 测试 JSON 提取的容错
    "nano-tools/whisperbox": "```json\n"
    + _payload(
        one_liner="没有显卡也能跑的语音转文字服务，专为低功耗 NAS 优化",
        tldr={
            "pain": "没显卡的话，Whisper 转写一小时录音要等大半天",
            "solution": "Rust 重写推理链路并做 CPU 量化，还能监听目录自动转写",
            "fit": "纯 CPU，内存峰值约 300MB，docker run 挂两个目录即可",
        },
        highlights=[
            "纯 CPU 推理，300MB 内存占用，群晖 J 系列低压 U 也能实时转写",
            "Rust 实现，冷启动 200ms，比 Python 版本快一个数量级",
            "提供 REST API 与 Watch 目录两种模式，可直接接入 NAS 的媒体库自动打字幕",
        ],
        target_audience="想给 NAS 上的录音、播客、会议视频自动生成字幕的自托管用户",
        difficulty="low",
        rating=4,
        rating_reason="定位精准、资源占用极低，是 NAS 场景下少有的能直接用的语音方案。",
        detailed_intro=(
            "Whisper 好用是好用，但官方实现对硬件有点挑剔——没显卡的话，"
            "转写一段一小时的会议录音可能要等上大半天。这对放在角落里的 NAS 来说基本等于不可用。\n\n"
            "WhisperBox 用 Rust 重写了推理链路，专门针对 CPU 做了量化和内存优化。"
            "实测在一台四核低压 U 的 NAS 上，转写速度大约是实时的 1.5 倍，**内存峰值只有 300MB 左右**。\n\n"
            "它提供两种用法：一种是标准的 REST API，你自己的脚本调用；"
            "另一种更贴合 NAS 场景——指定一个监听目录，往里丢音频文件，转写结果自动出现在旁边。"
            "配合 NAS 自带的文件夹同步，等于给整个媒体库加了自动字幕。\n\n"
            "上手基本没门槛，`docker run` 挂两个目录就完事了。模型文件首次启动会自动下载，"
            "small 模型大约 500MB，中文识别效果已经够用。\n\n"
            "**如果你的 NAS 里躺着一堆没整理过的录音，这个项目大概率能帮你把它们变成可搜索的文本。**"
        ),
        scores={"utility": 80, "problem_solving": 78, "popularity": 70, "nas_usability": 92},
    )
    + "\n```",
    # 非 JSON —— 测试降级分支
    "broken-json/llm-router": "抱歉，我无法分析这个仓库，README 内容不足以做出判断。",
    # 越界值与非法枚举 —— 测试夹紧与回退
    "edge-cases/promptforge": _payload(
        one_liner="给提示词做版本管理和自动评测，让 prompt 迭代有据可依",
        tldr={"pain": "同一个 prompt 三个版本散落各处，改坏了不知道是哪次改的"},
        highlights=[
            "Prompt 版本化与 diff，改动一目了然",
            "批量评测跑分，支持自定义评分函数",
        ],
        target_audience="需要协作维护大量提示词的 AI 产品团队",
        difficulty="MEDIUM-HIGH",  # 非法枚举 → 回退 medium
        rating=9,  # 越界 → 夹到 5
        rating_reason="工程化程度不错，但对个人开发者来说偏重。",
        detailed_intro=(
            "Prompt 写着写着就乱了——同一个功能三个版本散在代码、文档和某人的聊天记录里，"
            "改坏了也不知道是哪次改的。PromptForge 想解决的就是这件事。\n\n"
            "它把 prompt 当成代码来管：每次修改生成一个版本，可以 diff、可以回滚、可以打标签。"
            "更关键的是配套的评测能力——你准备一组测试用例和评分标准，改完 prompt 一键跑分，"
            "直接看到这次改动是变好了还是变差了。\n\n"
            "对个人开发者来说这套流程可能偏重，但如果团队里有三个以上的人在改同一批 prompt，"
            "它能省掉大量扯皮时间。"
        ),
        scores={"utility": 120, "problem_solving": -5, "popularity": 62, "nas_usability": 55},
    ),
}


class MockGitHubClient:
    """替换 GitHubClient，返回内置假仓库。"""

    def __init__(self, repos: list[Repo] | None = None) -> None:
        self.repos = repos if repos is not None else MOCK_REPOS

    def search_repos(self, *, days: int = 3, limit: int = 5, **_: Any) -> list[Repo]:
        LOGGER.info("[MOCK] 返回 %d 个假仓库（近 %d 天）", min(limit, len(self.repos)), days)
        return sorted(self.repos, key=lambda r: r.stars, reverse=True)[:limit]

    def fetch_readme(self, full_name: str, *, max_chars: int = 24_000) -> str:
        for repo in self.repos:
            if repo.full_name == full_name:
                return repo.readme[:max_chars]
        return ""

    def enrich(self, repos: list[Repo], *, max_chars: int = 24_000) -> list[Repo]:
        for repo in repos:
            repo.readme = self.fetch_readme(repo.full_name, max_chars=max_chars)
        return repos


class MockLLMClient:
    """替换 LLMClient，按仓库名返回预置回复。"""

    model = "mock-model"

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = responses if responses is not None else MOCK_LLM_RESPONSES
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002
        for full_name, reply in self.responses.items():
            if full_name in user:
                self.calls.append(full_name)
                LOGGER.info("[MOCK] LLM 返回预置结果: %s", full_name)
                return reply
        self.calls.append("<unknown>")
        return _payload(
            one_liner="这是一个 mock 生成的兜底摘要",
            tldr={"pain": "mock 痛点", "solution": "mock 方案", "fit": "mock 部署说明"},
            highlights=["mock 亮点 A", "mock 亮点 B"],
            target_audience="本地测试",
            difficulty="medium",
            rating=3,
            rating_reason="mock 数据",
            detailed_intro="这是 mock 模式下的兜底介绍文本。",
            scores={"utility": 50, "problem_solving": 50, "popularity": 50, "nas_usability": 50},
        )


class MockWeChatSession:
    """替换 requests.Session 注入 WeChatNotifier，把推送内容打到日志。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def post(self, url: str, json: dict, timeout: int = 20):  # noqa: A002, ARG002
        content = json["markdown"]["content"]
        self.sent.append(content)
        LOGGER.info("[MOCK] 企微推送内容预览:\n%s\n%s\n%s", "-" * 60, content, "-" * 60)

        class _Resp:
            ok = True
            status_code = 200
            text = '{"errcode":0,"errmsg":"ok"}'

            @staticmethod
            def json() -> dict:
                return {"errcode": 0, "errmsg": "ok"}

        return _Resp()
