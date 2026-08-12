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
    _, port = server
    status, body = _get(port, "/")
    assert status == 200
    assert "2026-08-12-acme_cool.html" in body
    assert "共 1 份报告" in body


def test_serves_report_file(server):
    _, port = server
    status, body = _get(port, "/reports/2026-08-12-acme_cool.html")
    assert status == 200
    assert "报告" in body


def test_empty_state(tmp_path):
    srv = report_server.start_background(tmp_path, 0)
    try:
        _, body = _get(srv.server_address[1], "/")
        assert "今日无新发现" in body
    finally:
        srv.shutdown()
