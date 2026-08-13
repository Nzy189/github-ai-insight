"""内置 HTTP 服务 — 对外提供 /reports 静态页面（PRD §4.3 方案 B）。

只读、无上传、路径穿越由 SimpleHTTPRequestHandler 的 translate_path 兜底。
"""

from __future__ import annotations

import html
import logging
import sqlite3
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOGGER = logging.getLogger(__name__)

INDEX_CSS = """
body{background:#0A0A0B;color:#FAFAFA;font-family:-apple-system,BlinkMacSystemFont,
'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;margin:0;padding:48px 24px;line-height:1.6}
.wrap{max-width:800px;margin:0 auto}
h1{font-size:28px;font-weight:600;letter-spacing:-.015em;margin:0 0 8px}
p.sub{color:#A1A1AA;font-size:14px;margin:0 0 32px}
ul{list-style:none;padding:0;margin:0}
li{background:#111113;border:1px solid #27272A;border-radius:12px;padding:16px 20px;margin-bottom:12px}
li a{color:#3B82F6;text-decoration:none;font-size:16px;font-weight:500;
font-family:'JetBrains Mono','SF Mono',Consolas,monospace;word-break:break-all}
li a:hover{color:#60A5FA;text-decoration:underline}
li .meta{color:#52525B;font-size:12px;margin-top:4px}
.empty{color:#52525B;text-align:center;padding:64px 0;font-size:14px;line-height:1.8}
h2{font-size:15px;font-weight:600;margin:28px 0 10px;color:#FAFAFA;
display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
h2 .n{font-family:'JetBrains Mono',Consolas,monospace;font-size:12px;color:#52525B}
h2 .hint{font-size:11px;font-weight:400;color:#52525B}
li .top{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
li .nm{font-family:'JetBrains Mono','SF Mono',Consolas,monospace;font-size:14px;
font-weight:500;color:#FAFAFA;word-break:break-all}
li .nm a{color:#3B82F6;text-decoration:none}
li .nm a:hover{color:#60A5FA;text-decoration:underline}
li .sc{font-family:'JetBrains Mono',Consolas,monospace;font-size:16px;font-weight:700;flex:0 0 auto}
.s-high{color:#4ADE80} .s-mid{color:#FBBF24} .s-low{color:#F87171}
li .ol{font-size:13px;color:#A1A1AA;line-height:1.5;margin-top:6px}
.b{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:500}
.b-ok{background:rgba(34,197,94,.15);color:#4ADE80}
.b-warn{background:rgba(245,158,11,.15);color:#FBBF24}
.b-err{background:rgba(239,68,68,.15);color:#F87171}
.b-idle{background:#18181B;color:#A1A1AA;border:1px solid #27272A}
.b-blk{background:rgba(59,130,246,.12);color:#60A5FA}
.mono{font-family:'JetBrains Mono',Consolas,monospace}
"""


class ReportRequestHandler(SimpleHTTPRequestHandler):
    """把 data_dir 作为根目录；/ 渲染报告索引；/health 供 Docker 健康检查。"""

    def __init__(self, *args, directory: str, **kwargs) -> None:
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        LOGGER.debug("HTTP %s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/health":
            self._send_text(200, "ok")
            return
        if path in ("/", "/reports"):
            self._send_html(200, self._render_index())
            return
        super().do_GET()

    # ------------------------------------------------------------------ 工具

    def _send_text(self, code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ------------------------------------------------------------------ 首页

    def _read_records(self) -> list[dict]:
        """从 SQLite 读全部记录。数据库是二进制文件，NAS 上没终端就看不了，
        这个页面是唯一的可视入口，所以读失败也只能降级成空列表而不是报错。"""
        db_path = Path(self.directory) / "github_ai_insight.db"
        if not db_path.exists():
            return []
        try:
            # 只读打开，绝不干扰正在写库的调度进程
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM projects "
                    "ORDER BY COALESCE(pushed_at, fetched_at) DESC, total_score DESC"
                ).fetchall()
            finally:
                conn.close()
            return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            LOGGER.warning("读取数据库失败: %s", exc)
            return []

    @staticmethod
    def _score_class(score: float) -> str:
        return "s-high" if score >= 80 else ("s-mid" if score >= 50 else "s-low")

    def _row_html(self, r: dict, existing: set[str]) -> str:
        score = float(r.get("total_score") or 0)
        name = html.escape(str(r.get("repo_name") or ""))
        one = html.escape(str(r.get("one_liner") or ""))
        status = str(r.get("status") or "")
        model = html.escape(str(r.get("llm_model") or "—"))
        stamp = str(r.get("pushed_at") or r.get("fetched_at") or "")[:10]

        badge = {
            "pushed": '<span class="b b-ok">已推送</span>',
            "degraded": '<span class="b b-warn">降级推送</span>',
            "failed": '<span class="b b-err">推送失败</span>',
            "skipped": '<span class="b b-idle">候补中</span>',
        }.get(status, f'<span class="b b-idle">{html.escape(status)}</span>')

        if r.get("from_backlog"):
            badge += ' <span class="b b-blk">往期精选</span>'

        # report_path 是写入时那台机器的路径，对外只能按文件名找。
        # 不能用 Path().name：库可能是在 Windows 上生成的（存的是反斜杠），
        # 而这里跑在 Linux 容器里，POSIX 不把 \ 当分隔符，整串会被当成文件名。
        fname = str(r.get("report_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        title = (f'<a href="/reports/{html.escape(fname)}">{name}</a>'
                 if fname and fname in existing else name)

        return (
            f'<li><div class="top"><span class="nm">{title}</span>'
            f'<span class="sc {self._score_class(score)}">{score:.1f}</span></div>'
            f'<div class="ol">{one}</div>'
            f'<div class="meta">{stamp} · {badge} · <span class="mono">{model}</span></div></li>'
        )

    def _render_index(self) -> str:
        reports_dir = Path(self.directory) / "reports"
        existing = {f.name for f in reports_dir.glob("*.html")} if reports_dir.exists() else set()
        records = self._read_records()

        if not records:
            # 数据库缺失或读不出来时，退回按文件列。报告就在磁盘上，
            # 不能因为读不到库就假装什么都没有。
            if existing:
                body = "<ul>" + "".join(
                    f'<li><div class="top"><span class="nm">'
                    f'<a href="/reports/{html.escape(n)}">{html.escape(n)}</a>'
                    f"</span></div></li>"
                    for n in sorted(existing, reverse=True)
                ) + "</ul>"
                stat = f"{len(existing)} 份报告（数据库不可读，仅按文件列出）"
            else:
                body = ('<div class="empty">还没有任何记录<br>'
                        '第一次执行完成后这里会出现内容</div>')
                stat = ""
        else:
            pushed = [r for r in records if r.get("status") in ("pushed", "degraded")]
            backlog = [r for r in records if r.get("status") in ("skipped", "failed")]
            sections = []
            if pushed:
                sections.append(
                    f'<h2>已推送 <span class="n">{len(pushed)}</span></h2><ul>'
                    + "".join(self._row_html(r, existing) for r in pushed) + "</ul>")
            if backlog:
                top = max(float(r.get("total_score") or 0) for r in backlog)
                sections.append(
                    f'<h2>候补池 <span class="n">{len(backlog)}</span>'
                    f'<span class="hint">当日新项目低于 {top:.1f} 分时会从这里顶上</span></h2><ul>'
                    + "".join(self._row_html(r, existing)
                              for r in sorted(backlog,
                                              key=lambda x: -(float(x.get("total_score") or 0))))
                    + "</ul>")
            body = "".join(sections)
            stat = f"{len(records)} 个项目 · {len(existing)} 份报告"

        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='color-scheme' content='dark'>"
            "<title>GitHub AI Insight</title>"
            f"<style>{INDEX_CSS}</style></head><body><div class='wrap'>"
            "<h1>GitHub AI Insight</h1>"
            f"<p class='sub'>{stat}</p>{body}"
            "</div></body></html>"
        )


def create_server(data_dir: Path, port: int) -> ThreadingHTTPServer:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)
    handler = partial(ReportRequestHandler, directory=str(data_dir))
    return ThreadingHTTPServer(("0.0.0.0", port), handler)


def serve_forever(data_dir: Path, port: int) -> None:
    server = create_server(data_dir, port)
    LOGGER.info("报告 HTTP 服务已启动: http://0.0.0.0:%d/reports", port)
    server.serve_forever()


def start_background(data_dir: Path, port: int) -> ThreadingHTTPServer:
    """在后台线程启动，返回 server 以便调用方 shutdown()。"""
    server = create_server(data_dir, port)
    thread = threading.Thread(target=server.serve_forever, name="report-http", daemon=True)
    thread.start()
    LOGGER.info("报告 HTTP 服务已启动（后台）: http://0.0.0.0:%d/reports", port)
    return server
