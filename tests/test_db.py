from __future__ import annotations

import pytest

from db import Database


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


def row(name: str, status: str = "pushed", score: float = 80.0) -> dict:
    return {
        "repo_name": name,
        "repo_url": f"https://github.com/{name}",
        "description": "desc",
        "language": "Python",
        "topics": '["ai"]',
        "stars": 100,
        "ai_summary": "{}",
        "one_liner": "one liner",
        "difficulty": "medium",
        "rating": 4,
        "total_score": score,
        "report_path": "/tmp/r.html",
        "status": status,
        "error_message": "",
    }


class TestSchema:
    def test_init_is_idempotent(self, tmp_path):
        path = tmp_path / "x.db"
        Database(path)
        Database(path)  # 不应报错
        assert path.exists()

    def test_creates_parent_dirs(self, tmp_path):
        db = Database(tmp_path / "deep" / "nested" / "x.db")
        assert db.path.parent.is_dir()


class TestDedup:
    def test_unknown_repo_is_new(self, db):
        assert db.is_already_pushed("a/b") is False

    def test_pushed_repo_is_blocked(self, db):
        db.save_project(row("a/b", "pushed"), mark_pushed=True)
        assert db.is_already_pushed("a/b") is True

    def test_degraded_also_counts_as_pushed(self, db):
        db.save_project(row("a/b", "degraded"), mark_pushed=True)
        assert db.is_already_pushed("a/b") is True

    @pytest.mark.parametrize("status", ["skipped", "failed"])
    def test_not_pushed_statuses_stay_eligible(self, db, status):
        db.save_project(row("a/b", status))
        assert db.is_already_pushed("a/b") is False

    def test_filter_new(self, db):
        db.save_project(row("a/pushed", "pushed"), mark_pushed=True)
        db.save_project(row("a/skipped", "skipped"))
        result = db.filter_new(["a/pushed", "a/skipped", "a/brand-new"])
        assert result == {"a/skipped", "a/brand-new"}

    def test_filter_new_empty_input(self, db):
        assert db.filter_new([]) == set()


class TestUpsert:
    def test_repo_name_is_unique(self, db):
        db.save_project(row("a/b", "skipped", 50.0))
        db.save_project(row("a/b", "pushed", 91.5), mark_pushed=True)
        assert db.count() == 1
        record = db.recent(1)[0]
        assert record["status"] == "pushed"
        assert record["total_score"] == pytest.approx(91.5)

    def test_pushed_at_only_when_marked(self, db):
        db.save_project(row("a/no-push", "skipped"))
        db.save_project(row("a/pushed", "pushed"), mark_pushed=True)
        by_name = {r["repo_name"]: r for r in db.recent(10)}
        assert by_name["a/no-push"]["pushed_at"] is None
        assert by_name["a/pushed"]["pushed_at"] is not None

    def test_fetched_at_autofilled(self, db):
        db.save_project(row("a/b"))
        assert db.recent(1)[0]["fetched_at"]

    def test_recent_ordering(self, db):
        for i in range(3):
            db.save_project(row(f"a/repo{i}", "pushed", 50 + i), mark_pushed=True)
        assert db.count() == 3
        assert len(db.recent(2)) == 2
