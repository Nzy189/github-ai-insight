from __future__ import annotations

import urllib.request

import pytest

import report_server


@pytest.fixture
def server(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    (reports / "2026-08-12-acme_cool.html").write_text("<h1>报告</h1>", encoding="utf-8")

    srv = report_server.start_background(tmp_path, 0)
    yield srv, srv.server_address[1]
    srv.shutdown()


def _get(port: int, path: str) -> tuple[int, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def test_health(server):
    _, port = server
    status, body = _get(port, "/health")
    assert status == 200
    assert body == "ok"


def test_index_lists_reports(server):
    """没有数据库时退回按文件列 —— 报告就在磁盘上，不能因为读不到库就当作没有。"""
    _, port = server
    status, body = _get(port, "/")
    assert status == 200
    assert "2026-08-12-acme_cool.html" in body
    assert "数据库不可读" in body


def test_serves_report_file(server):
    _, port = server
    status, body = _get(port, "/reports/2026-08-12-acme_cool.html")
    assert status == 200
    assert "报告" in body


def test_empty_state(tmp_path):
    srv = report_server.start_background(tmp_path, 0)
    try:
        _, body = _get(srv.server_address[1], "/")
        assert "还没有任何记录" in body
    finally:
        srv.shutdown()


def test_index_reads_database(tmp_path):
    """有数据库时按记录渲染：分数、状态、模型、候补池分区。"""
    import sqlite3
    from db import Database

    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "2026-08-12-a_win.html").write_text("x", encoding="utf-8")
    db = Database(tmp_path / "github_ai_insight.db")
    db.save_project({
        "repo_name": "a/win", "repo_url": "u", "one_liner": "推送过的项目",
        "total_score": 88.5, "status": "pushed", "llm_model": "glm-5.2",
        "report_path": "/app/data/reports/2026-08-12-a_win.html", "from_backlog": 0,
    }, mark_pushed=True)
    db.save_project({
        "repo_name": "a/wait", "repo_url": "u", "one_liner": "候补里的项目",
        "total_score": 71.0, "status": "skipped", "llm_model": "glm-5.2",
    })

    srv = report_server.start_background(tmp_path, 0)
    try:
        _, body = _get(srv.server_address[1], "/")
    finally:
        srv.shutdown()

    assert "已推送" in body and "候补池" in body
    assert "推送过的项目" in body and "候补里的项目" in body
    assert "88.5" in body and "71.0" in body
    assert "glm-5.2" in body
    assert 'href="/reports/2026-08-12-a_win.html"' in body   # 有报告文件才给链接
    assert "当日新项目低于 71.0 分时会从这里顶上" in body


def test_index_survives_corrupt_database(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "2026-08-12-x.html").write_text("x", encoding="utf-8")
    (tmp_path / "github_ai_insight.db").write_bytes(b"not a database")

    srv = report_server.start_background(tmp_path, 0)
    try:
        status, body = _get(srv.server_address[1], "/")
    finally:
        srv.shutdown()

    assert status == 200
    assert "2026-08-12-x.html" in body
