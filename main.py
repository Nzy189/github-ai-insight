"""GitHub AI Insight — 主入口：流程编排 + APScheduler 调度 + CLI。

用法见 README.md / LOCAL_TESTING.md，或 `python main.py --help`。
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from pydantic import ValidationError

from ai_analyzer import AIAnalyzer, LLMClient, pick_winner, restore_from_backlog
from config import Settings, apply_tls_settings, load_settings, setup_logging
from db import Database
from github_client import GitHubClient, GitHubError
from models import AnalyzedProject
from report_generator import ReportGenerator
from wechat_notifier import WeChatNotifier

LOGGER = logging.getLogger("main")


# ====================================================================== 结果


@dataclass(slots=True)
class RunSummary:
    """一次执行的结果，便于测试断言与日志汇总。"""

    ok: bool = False
    reason: str = ""
    fetched: int = 0
    candidates: int = 0
    winner: AnalyzedProject | None = None
    report_path: str = ""
    archive_path: str = ""
    report_url: str = ""
    pushed: bool = False
    degraded: bool = False
    from_backlog: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "fetched": self.fetched,
            "candidates": self.candidates,
            "winner": self.winner.repo.full_name if self.winner else None,
            "total_score": self.winner.total_score if self.winner else None,
            "report_path": self.report_path,
            "archive_path": self.archive_path,
            "report_url": self.report_url,
            "pushed": self.pushed,
            "degraded": self.degraded,
            "from_backlog": self.from_backlog,
            "errors": self.errors,
        }


def _backlog_repo_still_alive(github: Any, project: AnalyzedProject) -> bool:
    """推候补项目前确认仓库还在。客户端不支持检查时一律放行。"""
    check = getattr(github, "repo_exists", None)
    if check is None:
        return True
    try:
        return bool(check(project.repo.full_name))
    except Exception:  # noqa: BLE001 — 检查本身出错不该拦住推送
        LOGGER.warning("仓库存活检查异常，按存活处理: %s", project.repo.full_name)
        return True


# ====================================================================== 组装


def build_components(settings: Settings) -> dict[str, Any]:
    """按配置装配各组件。mock 模式下换成假客户端，其余逻辑完全一致。"""
    settings.ensure_dirs()

    if settings.mock_mode:
        from mock_data import MockGitHubClient, MockLLMClient, MockWeChatSession

        github: Any = MockGitHubClient()
        llm: Any = MockLLMClient()
        notifier = WeChatNotifier(
            settings.wechat_webhook_url or "mock://webhook",
            session=MockWeChatSession(),  # type: ignore[arg-type]
            retry_delays=(),
        )
        model_name = "mock-model"
    else:
        github = GitHubClient(settings.github_token)
        llm = (
            LLMClient(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                provider=settings.llm_provider,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                session=requests.Session(),
            )
            if settings.llm_api_key
            else None
        )
        notifier = WeChatNotifier(settings.wechat_webhook_url)
        model_name = settings.llm_model

    return {
        "db": Database(settings.db_path),
        "github": github,
        "analyzer": AIAnalyzer(llm),
        "generator": ReportGenerator(
            settings.reports_dir,
            settings.archive_dir,
            report_base_url=settings.report_base_url,
            model_name=model_name,
        ),
        "notifier": notifier,
    }


# ====================================================================== 流程


def run_once(settings: Settings, *, report_date: date | None = None,
             components: dict[str, Any] | None = None) -> RunSummary:
    """执行一次完整流程：抓取 → 去重 → 分析 → 选优 → 报告 → 推送 → 归档。"""
    report_date = report_date or datetime.now(ZoneInfo(settings.timezone)).date()
    comps = components or build_components(settings)
    db: Database = comps["db"]
    github = comps["github"]
    analyzer: AIAnalyzer = comps["analyzer"]
    generator: ReportGenerator = comps["generator"]
    notifier: WeChatNotifier = comps["notifier"]

    summary = RunSummary()
    LOGGER.info("=" * 64)
    LOGGER.info("开始执行 | 日期 %s | 时区 %s", report_date, settings.timezone)

    # --- 1. 抓取 -------------------------------------------------------
    try:
        repos = github.search_repos(
            days=settings.search_days,
            limit=settings.candidate_count,
            min_stars=settings.min_stars,
        )
    except GitHubError as exc:
        LOGGER.error("GitHub 抓取失败，本次跳过: %s", exc)
        summary.reason = f"GitHub 抓取失败: {exc}"
        summary.errors.append(str(exc))
        return summary

    summary.fetched = len(repos)
    LOGGER.info("抓取到 %d 个仓库", len(repos))

    # --- 2. 去重 -------------------------------------------------------
    new_names = db.filter_new([r.full_name for r in repos])
    candidates = [r for r in repos if r.full_name in new_names]
    summary.candidates = len(candidates)
    LOGGER.info("去重后剩余 %d 个候选", len(candidates))

    # --- 3. 分析打分 ---------------------------------------------------
    projects: list[AnalyzedProject] = []
    if candidates:
        github.enrich(candidates, max_chars=settings.readme_max_chars)
        projects = analyzer.analyze_all(candidates)
        for p in sorted(projects, key=lambda x: x.total_score, reverse=True):
            LOGGER.info(
                "  %-40s 总分 %5.1f %s",
                p.repo.full_name, p.total_score, "(降级)" if p.analysis.degraded else "",
            )
    else:
        LOGGER.info("今日无新项目 —— 直接看候补池")

    # --- 4. 选优：今天的候选 ∪ 候补池，取全局最高分 ----------------------
    # 每天分析 5 个只推 1 个，落选的 4 个过了 GitHub「近 3 天」的搜索窗口
    # 就再也不会出现在候选里。它们的完整分析结果还在库里，当天所有新项目
    # 都打不过其中某一个时，就把那个顶上来推 —— 无需再调 LLM。
    today_winner = pick_winner(projects)
    winner = today_winner
    backlog_row = db.best_backlog()

    if backlog_row is not None:
        backlog_score = float(backlog_row.get("total_score") or 0)
        today_score = today_winner.total_score if today_winner else -1.0
        LOGGER.info(
            "候补池 %d 条，最高 %s (%.1f) vs 今日最高 %.1f",
            db.backlog_size(), backlog_row.get("repo_name"), backlog_score, today_score,
        )
        if backlog_score > today_score:
            restored = restore_from_backlog(backlog_row)
            if restored is not None and _backlog_repo_still_alive(github, restored):
                winner = restored
                summary.from_backlog = True
                LOGGER.info("改用候补池项目（当日新项目均未超过它）")

    if winner is None:
        LOGGER.info("今日无候选，候补池也是空的")
        summary.reason = "去重后无候选项目"
        summary.ok = True
        if settings.notify_empty and not settings.dry_run:
            result = notifier.push_empty(report_date)
            summary.pushed = result.ok
            if not result.ok and not result.skipped:
                summary.errors.append(result.message)
        return summary

    summary.winner = winner
    summary.degraded = winner.analysis.degraded
    LOGGER.info(
        "🏆 胜出: %s (%.1f 分)%s",
        winner.repo.full_name, winner.total_score,
        f" —— 往期精选，分析于 {winner.backlog_analyzed_at}" if winner.from_backlog else "",
    )

    # --- 5. 生成报告 + 归档 --------------------------------------------
    report_path, report_url = generator.write_report(winner, report_date)
    summary.report_path, summary.report_url = str(report_path), report_url

    # --- 6. 推送 -------------------------------------------------------
    if settings.dry_run:
        LOGGER.info("dry-run 模式，跳过企微推送")
        winner.status = "skipped"
    else:
        result = notifier.push_project(winner, report_date)
        summary.pushed = result.ok
        if result.ok:
            winner.status = "degraded" if winner.analysis.degraded else "pushed"
        elif result.skipped:
            winner.status = "skipped"  # 未配置 webhook：明天仍可重推
        else:
            winner.status = "failed"
            winner.error_message = result.message
            summary.errors.append(result.message)

    # --- 7. 归档 + 落库 ------------------------------------------------
    summary.archive_path = str(generator.write_archive(winner, report_date))

    db.save_project(winner.to_db_row(), mark_pushed=summary.pushed)
    for loser in projects:
        if loser is winner:
            continue
        loser.status = "skipped"
        db.save_project(loser.to_db_row())
        # 落选项目进候补池。SQLite 是候补池的唯一副本，NAS 上那个库
        # 损坏就全没了 —— 顺手落一份 Markdown 便于人工恢复。
        try:
            generator.write_archive(loser, report_date, subdir="backlog")
        except OSError as exc:
            LOGGER.warning("候补归档写入失败 %s: %s", loser.repo.full_name, exc)

    summary.ok = True
    summary.reason = "完成"
    LOGGER.info("执行完成 | 报告 %s | 推送 %s", report_path.name, "成功" if summary.pushed else "未发送")
    LOGGER.info("=" * 64)
    return summary


# ====================================================================== 调度


def run_scheduler(settings: Settings) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    tz = ZoneInfo(settings.timezone)
    hour, minute = settings.execution_time.split(":")

    server = None
    if settings.serve_reports:
        import report_server

        server = report_server.start_background(settings.data_dir, settings.http_port)

    _startup_llm_check(settings)

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        lambda: _safe_run(settings),
        CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
        id="daily_insight",
        name="GitHub AI 日报",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    next_run = scheduler.get_jobs()[0].next_run_time if scheduler.get_jobs() else None
    LOGGER.info("调度已启动 | 每日 %s (%s)", settings.execution_time, settings.timezone)
    LOGGER.info("下次执行: %s", next_run)

    def _shutdown(signum, frame):  # noqa: ANN001, ARG001
        LOGGER.info("收到信号 %s，正在退出...", signum)
        scheduler.shutdown(wait=False)
        if server:
            server.shutdown()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, AttributeError):  # 非主线程 / 平台不支持
            pass

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("调度器已停止")
    finally:
        if server:
            server.shutdown()


def _startup_llm_check(settings: Settings) -> None:
    """常驻启动时验一次 LLM 配置，结果只写日志，绝不阻止启动。

    换模型或换 API 之后重启容器，配错了这里立刻报出来；否则要等到
    第二天执行时间收到一条全降级的日报才会发现。
    """
    if settings.mock_mode or not settings.startup_llm_check:
        return
    if not settings.llm_api_key:
        LOGGER.warning("启动自检：LLM_API_KEY 未配置，分析将全部降级")
        return

    from ai_analyzer import FatalLLMError, LLMError, extract_json

    LOGGER.info("启动自检：验证 LLM 配置 %s @ %s", settings.llm_model, settings.llm_base_url)
    client = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        provider=settings.llm_provider,
        timeout=min(settings.llm_timeout, 60),
        max_tokens=256,
        session=requests.Session(),
    )
    try:
        reply = client.complete(
            "你是一个测试助手，只输出 JSON。",
            '请原样返回：{"ok": true}',
        )
    except (FatalLLMError, LLMError) as exc:
        LOGGER.error("⚠️ 启动自检失败：%s —— 分析会全部降级，请检查 .env 后重启容器", exc)
        return

    try:
        extract_json(reply)
    except ValueError:
        LOGGER.warning("启动自检：模型可连通但未返回可解析 JSON，可能频繁降级；建议换模型")
        return
    LOGGER.info("启动自检通过 ✓")


def _safe_run(settings: Settings) -> None:
    """调度回调包一层，任何异常都不能让调度器挂掉。"""
    try:
        run_once(settings)
    except Exception:  # noqa: BLE001
        LOGGER.exception("定时任务执行异常")


# ====================================================================== CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="GitHub AI Insight — 每日 AI 开源项目日报",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python main.py --now                    立即执行一次（真实 API）
  python main.py --now --mock --open      本地假数据跑通全链路并打开报告
  python main.py --now --dry-run          执行但不推送企微
  python main.py --now --candidates 10    临时调大候选数量
  python main.py --show-config            查看当前配置
  python main.py --test-llm               验证 LLM Key / 地址 / 模型是否可用
  python main.py --serve                  只启动报告 HTTP 服务
  python main.py --list                   查看数据库最近记录
  python main.py                          常驻：定时调度 + HTTP 服务
""",
    )
    parser.add_argument("--now", action="store_true", help="立即执行一次后退出")
    parser.add_argument("--mock", action="store_true",
                        help="本地测试：使用内置假数据，不发起任何网络请求")
    parser.add_argument("--dry-run", action="store_true", help="生成报告与归档，但不推送企微")
    parser.add_argument("--open", dest="open_report", action="store_true",
                        help="执行完成后用浏览器打开生成的报告")
    parser.add_argument("--serve", action="store_true", help="仅启动报告 HTTP 服务")
    parser.add_argument("--show-config", action="store_true", help="打印当前配置后退出")
    parser.add_argument("--test-llm", action="store_true",
                        help="只发一次最小请求，验证 LLM Key / 地址 / 模型是否可用")
    parser.add_argument("--list", dest="list_db", action="store_true", help="打印数据库最近记录")
    parser.add_argument("--model", help="覆盖 LLM_MODEL")
    parser.add_argument("--candidates", type=int, help="覆盖 CANDIDATE_COUNT")
    parser.add_argument("--days", type=int, help="覆盖 SEARCH_DAYS")
    parser.add_argument("--data-dir", help="覆盖 DATA_DIR")
    parser.add_argument("--port", type=int, help="覆盖 HTTP_PORT")
    parser.add_argument("--log-level", help="覆盖 LOG_LEVEL")
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    data_dir = args.data_dir
    if data_dir is None and args.mock:
        # mock 数据不污染真实归档目录
        data_dir = "./data-local"

    return load_settings(
        llm_model=args.model,
        candidate_count=args.candidates,
        search_days=args.days,
        data_dir=Path(data_dir) if data_dir else None,
        http_port=args.port,
        log_level=args.log_level.upper() if args.log_level else None,
        mock_mode=True if args.mock else None,
        dry_run=True if args.dry_run else None,
    )


def _print_config(settings: Settings) -> None:
    print(json.dumps(settings.redacted_dump(), indent=2, ensure_ascii=False))
    print()
    print(f"  数据库:   {settings.db_path}")
    print(f"  报告目录: {settings.reports_dir}")
    print(f"  归档目录: {settings.archive_dir}")
    warnings = settings.validation_warnings()
    if warnings:
        print("\n配置提示:")
        for w in warnings:
            print(f"  ! {w}")


def _test_llm(settings: Settings) -> int:
    """发一次最小 LLM 请求，验证配置是否可用。

    刻意不走完整 Prompt —— 这样失败就一定是连接/鉴权/模型名的问题，
    而不是 Prompt 或 README 内容导致的。
    """
    from ai_analyzer import FatalLLMError, LLMError, extract_json

    print(f"端点:   {settings.llm_base_url}")
    print(f"模型:   {settings.llm_model}")
    print(f"协议:   {settings.llm_provider}")
    key = settings.llm_api_key
    print(f"Key:    {'<未配置>' if not key else f'已配置 (长度 {len(key)}, 结尾 ...{key[-4:]})'}")
    print()

    if not key:
        print("❌ LLM_API_KEY 未配置 —— 请在 .env 中填写后重试")
        return 1

    client = LLMClient(
        api_key=key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        provider=settings.llm_provider,
        timeout=settings.llm_timeout,
        max_tokens=256,
        session=requests.Session(),
    )

    started = datetime.now()
    try:
        reply = client.complete(
            "你是一个测试助手，只输出 JSON。",
            '请原样返回这个 JSON，不要有任何其他内容：{"ok": true, "msg": "连接正常"}',
        )
    except FatalLLMError as exc:
        print(f"❌ 鉴权或配额失败: {exc}")
        print("   → 401 检查 LLM_API_KEY；402/429 检查余额与限流；"
              "若用的是中转服务，确认 LLM_BASE_URL 结尾是否需要 /v1")
        return 1
    except LLMError as exc:
        print(f"❌ 调用失败: {exc}")
        print("   → 常见原因：LLM_BASE_URL 写错、模型名不存在、网络不通（需要代理）")
        return 1

    elapsed = (datetime.now() - started).total_seconds()
    print(f"✅ 调用成功，耗时 {elapsed:.1f}s")
    print(f"原始回复: {reply.strip()[:300]}")

    try:
        extract_json(reply)
    except ValueError:
        print("⚠️  该模型未能返回可解析的 JSON。流程仍可运行，但很可能频繁降级；")
        print("   建议换一个指令跟随能力更强的模型。")
        return 0

    print("✅ JSON 解析正常 —— 配置可用，可以执行 --now --dry-run 了")
    return 0


def _print_db(settings: Settings) -> None:
    db = Database(settings.db_path)
    rows = db.recent(20)
    if not rows:
        print("数据库为空。")
        return
    print(f"共 {db.count()} 条记录，最近 {len(rows)} 条：\n")
    print(f"{'日期':<12} {'状态':<9} {'分数':>6}  仓库")
    print("-" * 76)
    for r in rows:
        stamp = (r.get("pushed_at") or r.get("fetched_at") or "")[:10]
        print(f"{stamp:<12} {r['status'] or '-':<9} {r['total_score'] or 0:>6.1f}  {r['repo_name']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = settings_from_args(args)
    except ValidationError as exc:
        # 配置错误不该甩一坨堆栈出来 —— 在容器里那是最难读的失败方式
        print("❌ 配置校验失败，请检查 .env：\n", file=sys.stderr)
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"])
            print(f"  {field}: {err['msg']}（当前值: {err.get('input')!r}）", file=sys.stderr)
        return 2

    setup_logging(settings.log_level)
    apply_tls_settings(settings)

    if args.show_config:
        _print_config(settings)
        return 0

    if args.list_db:
        _print_db(settings)
        return 0

    if args.test_llm:
        return _test_llm(settings)

    for warning in settings.validation_warnings():
        LOGGER.warning("%s", warning)

    if args.serve:
        import report_server

        report_server.serve_forever(settings.data_dir, settings.http_port)
        return 0

    if args.now:
        summary = run_once(settings)
        print()
        print(json.dumps(summary.as_dict(), indent=2, ensure_ascii=False))
        if args.open_report and summary.report_path:
            webbrowser.open(Path(summary.report_path).resolve().as_uri())
        return 0 if summary.ok else 1

    run_scheduler(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
