"""SQLite 持久化 — 建表、去重查询、写入。

数据量极小（每天 1-5 条），不做分区/清理，长期保留作历史记录。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_name     TEXT NOT NULL UNIQUE,
    repo_url      TEXT,
    description   TEXT,
    language      TEXT,
    topics        TEXT,
    stars         INTEGER DEFAULT 0,
    ai_summary    TEXT,
    one_liner     TEXT,
    difficulty    TEXT,
    rating        INTEGER,
    total_score   REAL,
    report_path   TEXT,
    fetched_at    TIMESTAMP,
    pushed_at     TIMESTAMP,
    status        TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_pushed_at ON projects(pushed_at);
CREATE INDEX IF NOT EXISTS idx_projects_backlog ON projects(status, total_score DESC);
"""

_PUSHED_STATUSES = ("pushed", "degraded")
# 分析过但从未推送出去的项目 —— 候补池。
# 这些记录带着完整的 LLM 分析 JSON，重新拿来推送时无需再调模型。
_BACKLOG_STATUSES = ("skipped", "failed")


class Database:
    """轻量 SQLite 封装。每次操作独立开关连接，避免线程亲和问题（调度器线程池）。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            # NAS 可能突然断电。WAL 的崩溃恢复能力远好于默认的 delete journal，
            # 而这个库现在是候补池的唯一副本，损坏的代价比以前高得多。
            # 注意：WAL 不能用在网络文件系统上 —— DATA_DIR 必须是 NAS 本地路径，
            # 不能指向 SMB/NFS 挂载点。失败时退回默认模式而不是让程序起不来。
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    LOGGER.warning("无法启用 WAL（当前 %s）—— DATA_DIR 是否在网络挂载上？", mode)
            except sqlite3.Error as exc:
                LOGGER.warning("启用 WAL 失败，沿用默认 journal 模式: %s", exc)
            conn.executescript(SCHEMA)
        LOGGER.debug("SQLite 初始化完成: %s", self.path)

    # ------------------------------------------------------------------ 去重

    def is_already_pushed(self, repo_name: str) -> bool:
        """PRD §3.1: full_name 已存在且已推送过 → 跳过。

        degraded 也算推送过（消息已经发出去了），避免第二天重复打扰。
        """
        placeholders = ",".join("?" * len(_PUSHED_STATUSES))
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM projects WHERE repo_name = ? AND status IN ({placeholders}) LIMIT 1",
                (repo_name, *_PUSHED_STATUSES),
            ).fetchone()
        return row is not None

    def filter_new(self, repo_names: list[str]) -> set[str]:
        """批量去重：返回其中尚未推送过的名字集合。"""
        if not repo_names:
            return set()
        placeholders = ",".join("?" * len(repo_names))
        status_ph = ",".join("?" * len(_PUSHED_STATUSES))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT repo_name FROM projects "
                f"WHERE repo_name IN ({placeholders}) AND status IN ({status_ph})",
                (*repo_names, *_PUSHED_STATUSES),
            ).fetchall()
        seen = {r["repo_name"] for r in rows}
        return {name for name in repo_names if name not in seen}

    # ------------------------------------------------------------------ 写入

    def save_project(self, row: dict[str, Any], *, mark_pushed: bool = False) -> int:
        """UPSERT 一条记录，返回行 id。

        mark_pushed=True 时写入 pushed_at 时间戳（仅当日胜出并推送成功的项目）。
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = dict(row)
        payload["fetched_at"] = payload.get("fetched_at") or now
        payload["pushed_at"] = now if mark_pushed else payload.get("pushed_at")

        columns = list(payload.keys())
        col_sql = ", ".join(columns)
        val_sql = ", ".join(f":{c}" for c in columns)
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "repo_name")

        sql = (
            f"INSERT INTO projects ({col_sql}) VALUES ({val_sql}) "
            f"ON CONFLICT(repo_name) DO UPDATE SET {update_sql}"
        )
        with self.connect() as conn:
            cur = conn.execute(sql, payload)
            if cur.lastrowid:
                return int(cur.lastrowid)
            got = conn.execute(
                "SELECT id FROM projects WHERE repo_name = ?", (payload["repo_name"],)
            ).fetchone()
            return int(got["id"]) if got else -1

    # ------------------------------------------------------------------ 查询

    def best_backlog(self) -> dict[str, Any] | None:
        """候补池里总分最高的一条，没有则返回 None。

        候补池 = 分析过但从未推送出去的项目。每天分析 5 个只推 1 个，
        另外 4 个过了 GitHub 的「近 3 天」搜索窗口就再也不会出现在候选里，
        但它们的完整分析结果还在库里 —— 当天所有新项目都打不过它时就拿它顶上。
        """
        placeholders = ",".join("?" * len(_BACKLOG_STATUSES))
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM projects WHERE status IN ({placeholders}) "
                f"AND total_score IS NOT NULL "
                f"ORDER BY total_score DESC, stars DESC LIMIT 1",
                _BACKLOG_STATUSES,
            ).fetchone()
        return dict(row) if row else None

    def backlog_size(self) -> int:
        placeholders = ",".join("?" * len(_BACKLOG_STATUSES))
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) AS c FROM projects WHERE status IN ({placeholders})",
                    _BACKLOG_STATUSES,
                ).fetchone()["c"]
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY COALESCE(pushed_at, fetched_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"])
