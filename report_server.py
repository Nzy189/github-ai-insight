"""内置 HTTP 服务 — 对外提供 /reports 静态页面（PRD §4.3 方案 B）。

只读、无上传、路径穿越由 SimpleHTTPRequestHandler 的 translate_path 兜底。
"""

from __future__ import annotations

import html
import logging
import threading
from datetime import datetime
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
.empty{color:#52525B;text-align:center;padding:64px 0;font-size:14px}
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

    def _render_index(self) -> str:
        reports_dir = Path(self.directory) / "reports"
        files = sorted(reports_dir.glob("*.html"), reverse=True) if reports_dir.exists() else []

        if files:
            items = []
            for f in files:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                size_kb = f.stat().st_size / 1024
                items.append(
                    f'<li><a href="/reports/{html.escape(f.name)}">{html.escape(f.name)}</a>'
                    f'<div class="meta">{mtime} · {size_kb:.0f} KB</div></li>'
                )
            body = "<ul>" + "".join(items) + "</ul>"
        else:
            body = '<div class="empty">今日无新发现 — 还没有生成任何报告</div>'

        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='color-scheme' content='dark'>"
            "<title>GitHub AI Insight — 报告归档</title>"
            f"<style>{INDEX_CSS}</style></head><body><div class='wrap'>"
            "<h1>GitHub AI Insight</h1>"
            f"<p class='sub'>共 {len(files)} 份报告</p>{body}"
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
