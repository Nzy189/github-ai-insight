"""配置管理 — pydantic-settings 从 .env / 环境变量加载。

命令行参数优先级高于 .env，通过 `Settings.with_overrides()` 合并。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOGGER = logging.getLogger(__name__)

# 评分维度权重（PRD §3.2）
SCORE_WEIGHTS: dict[str, float] = {
    "utility": 0.35,
    "problem_solving": 0.30,
    "popularity": 0.25,
    "nas_usability": 0.10,
}

SCORE_LABELS_CN: dict[str, str] = {
    "utility": "实用性",
    "problem_solving": "解决问题",
    "popularity": "受欢迎程度",
    "nas_usability": "NAS 可用性",
}


class Settings(BaseSettings):
    """全部运行时配置。字段名与 .env 中的大写变量一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- GitHub ---
    github_token: str = ""
    search_days: int = 3
    candidate_count: int = 5
    min_stars: int = 10
    readme_max_chars: int = 24_000  # ≈ 8000 token

    # --- LLM ---
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_timeout: int = 120
    llm_max_tokens: int = 4096
    # 常驻模式启动时发一次最小 LLM 请求验证配置。
    # 换模型/换 API 后重启容器，配错了立刻在 docker logs 里看到，
    # 而不是等到第二天 12:00 收到一条全降级的日报才发现。
    startup_llm_check: bool = True

    # --- 推送 ---
    wechat_webhook_url: str = ""
    notify_empty: bool = False
    report_base_url: str = "http://localhost:8080/reports"

    # --- 调度 ---
    execution_time: str = "12:00"
    timezone: str = "Asia/Dubai"

    # --- 运行时 ---
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    http_port: int = 8080
    serve_reports: bool = True

    # --- TLS 信任链 ---
    # 开发机上如果有代理/杀软在解密 HTTPS（MITM），Python 只认 certifi 自带的 CA 包，
    # 会报 CERTIFICATE_VERIFY_FAILED。以下两项让它认得系统里已有的信任链。
    # 二者都不会关闭证书校验。容器内通常不需要开。
    use_system_certs: bool = False   # 改用操作系统证书存储（需 pip install truststore）
    ca_bundle: str = ""              # 或直接指定一个 PEM 文件路径

    # --- 本地测试 ---
    mock_mode: bool = False
    dry_run: bool = False  # 生成报告与归档，但不推送企微

    @field_validator("execution_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        try:
            hour, minute = v.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"EXECUTION_TIME 必须是 HH:MM 格式，收到: {v!r}") from exc
        return v

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        level = v.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"LOG_LEVEL 非法: {v!r}")
        return level

    @field_validator("candidate_count", "search_days")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("必须为正整数")
        return v

    # --- 派生路径 ---
    @property
    def db_path(self) -> Path:
        return self.data_dir / "github_ai_insight.db"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def with_overrides(self, **overrides: Any) -> "Settings":
        """返回叠加了命令行参数的新配置（None 值被忽略）。"""
        clean = {k: v for k, v in overrides.items() if v is not None}
        if not clean:
            return self
        return self.model_copy(update=clean)

    # --- 自检 ---
    def validation_warnings(self) -> list[str]:
        """返回配置隐患列表（不抛异常，让系统以降级模式继续运行）。"""
        warnings: list[str] = []
        if self.mock_mode:
            return ["MOCK 模式已开启 — 不会发起任何真实网络请求"]
        if not self.llm_api_key:
            warnings.append("LLM_API_KEY 未配置 — AI 分析将全部降级为 GitHub 元数据")
        if not self.github_token:
            warnings.append("GITHUB_TOKEN 未配置 — 使用匿名请求，速率限制 60 次/小时")
        if not self.wechat_webhook_url:
            warnings.append("WECHAT_WEBHOOK_URL 未配置 — 跳过推送，仅生成报告与归档")
        if self.report_base_url.startswith("http://localhost"):
            warnings.append("REPORT_BASE_URL 仍是 localhost — 企微里的报告链接在手机上打不开")
        return warnings

    def redacted_dump(self) -> dict[str, Any]:
        """用于 --show-config 输出，敏感字段脱敏。"""
        data = self.model_dump(mode="json")
        for key in ("github_token", "llm_api_key", "wechat_webhook_url"):
            value = data.get(key) or ""
            data[key] = f"<已配置 ...{value[-4:]}>" if value else "<未配置>"
        return data


def load_settings(**overrides: Any) -> Settings:
    return Settings().with_overrides(**overrides)


def apply_tls_settings(settings: Settings) -> None:
    """按配置扩展 TLS 信任链。必须在任何 HTTPS 请求之前调用。

    刻意不提供"关闭校验"的开关：那会让中间人对推送内容和 API Key 一览无余，
    而这两个选项都是在**扩展**信任链，不是绕过它。
    """
    import os

    if settings.use_system_certs:
        try:
            import truststore

            truststore.inject_into_ssl()
            LOGGER.info("已启用操作系统证书存储 (truststore)")
        except ImportError:
            LOGGER.error(
                "USE_SYSTEM_CERTS=true 但 truststore 未安装 —— 执行 pip install truststore"
            )

    if settings.ca_bundle:
        path = Path(settings.ca_bundle)
        if not path.is_file():
            LOGGER.error("CA_BUNDLE 指向的文件不存在: %s", path)
        else:
            os.environ["REQUESTS_CA_BUNDLE"] = str(path)
            os.environ["SSL_CERT_FILE"] = str(path)
            LOGGER.info("已加载自定义 CA 证书: %s", path)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
