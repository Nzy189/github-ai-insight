from __future__ import annotations

import pytest

from config import SCORE_WEIGHTS, Settings
from models import Repo, Scores


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.candidate_count == 5
        assert s.search_days == 3
        assert s.execution_time == "12:00"
        assert s.timezone == "Asia/Dubai"

    def test_derived_paths(self, tmp_path):
        s = Settings(data_dir=tmp_path / "d")
        assert s.db_path == tmp_path / "d" / "github_ai_insight.db"
        assert s.reports_dir == tmp_path / "d" / "reports"
        assert s.archive_dir == tmp_path / "d" / "archive"

    @pytest.mark.parametrize("bad", ["25:00", "12:99", "noon", "12"])
    def test_bad_execution_time(self, bad):
        with pytest.raises(ValueError):
            Settings(execution_time=bad)

    def test_bad_log_level(self):
        with pytest.raises(ValueError):
            Settings(log_level="LOUD")

    def test_overrides_ignore_none(self):
        s = Settings(llm_model="gpt-4o")
        assert s.with_overrides(llm_model=None).llm_model == "gpt-4o"
        assert s.with_overrides(llm_model="claude-3-5-sonnet").llm_model == "claude-3-5-sonnet"

    def test_secrets_redacted(self):
        s = Settings(llm_api_key="sk-verysecret1234", github_token="")
        dump = s.redacted_dump()
        assert "verysecret" not in dump["llm_api_key"]
        assert dump["llm_api_key"].endswith("1234>")
        assert dump["github_token"] == "<未配置>"

    def test_warnings_flag_missing_config(self):
        warnings = " ".join(Settings().validation_warnings())
        assert "LLM_API_KEY" in warnings
        assert "WECHAT_WEBHOOK_URL" in warnings

    def test_ensure_dirs(self, tmp_path):
        s = Settings(data_dir=tmp_path / "fresh")
        s.ensure_dirs()
        assert s.reports_dir.is_dir()
        assert s.archive_dir.is_dir()


class TestScores:
    def test_weights_sum_to_one(self):
        assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_weighted_total(self):
        # 90*.35 + 80*.30 + 70*.25 + 60*.10 = 31.5+24+17.5+6 = 79.0
        s = Scores(utility=90, problem_solving=80, popularity=70, nas_usability=60)
        assert s.total == pytest.approx(79.0)

    def test_all_hundred(self):
        assert Scores(100, 100, 100, 100).total == pytest.approx(100.0)

    def test_defaults_are_fifty(self):
        assert Scores().total == pytest.approx(50.0)


class TestRepo:
    def test_slug_is_filename_safe(self, repo):
        assert repo.slug == "acme_cool-agent"
        assert "/" not in repo.slug

    def test_repo_name(self, repo):
        assert repo.repo_name == "cool-agent"

    def test_from_api_handles_nulls(self):
        r = Repo.from_api({
            "full_name": "a/b",
            "html_url": "https://github.com/a/b",
            "description": None,
            "language": None,
            "topics": None,
            "stargazers_count": 12,
            "owner": {"login": "a"},
        })
        assert r.description == ""
        assert r.language == ""
        assert r.topics == []
        assert r.stars == 12
        assert r.owner == "a"


from models import Tldr


class TestTldr:
    def test_defaults_are_empty(self):
        t = Tldr()
        assert t.pain == ""
        assert t.solution == ""
        assert t.fit == ""
        assert t.is_empty is True

    def test_is_empty_false_when_any_field_set(self):
        assert Tldr(pain="x").is_empty is False
        assert Tldr(solution="x").is_empty is False
        assert Tldr(fit="x").is_empty is False

    def test_as_dict(self):
        t = Tldr(pain="痛", solution="解", fit="配")
        assert t.as_dict() == {"pain": "痛", "solution": "解", "fit": "配"}

    def test_analysis_carries_tldr(self, analysis):
        assert isinstance(analysis.tldr, Tldr)
        assert "tldr" in analysis.as_dict()
        assert analysis.as_dict()["tldr"] == analysis.tldr.as_dict()


class TestEnvFileDiscovery:
    """配置文件查找 —— GUI 型 NAS 上 .env 是隐藏文件，得支持看得见的名字。"""

    def test_config_env_is_read(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.env").write_text("LLM_MODEL=来自config.env\n", encoding="utf-8")
        assert Settings().llm_model == "来自config.env"

    def test_config_env_wins_over_dotenv(self, tmp_path, monkeypatch):
        """两个都在时，用户在文件管理器里看得见的那个说了算。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("LLM_MODEL=隐藏的\n", encoding="utf-8")
        (tmp_path / "config.env").write_text("LLM_MODEL=可见的\n", encoding="utf-8")
        assert Settings().llm_model == "可见的"

    def test_data_dir_config_env_is_read(self, tmp_path, monkeypatch):
        """容器场景：只挂了数据目录，配置放在里面。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "config.env").write_text("EXECUTION_TIME=05:45\n", encoding="utf-8")
        assert Settings().execution_time == "05:45"

    def test_loaded_env_files_reports_what_was_used(self, tmp_path, monkeypatch):
        from config import loaded_env_files

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.env").write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
        found = loaded_env_files()
        assert len(found) == 1
        assert found[0].endswith("config.env")

    def test_no_config_file_uses_defaults(self, tmp_path, monkeypatch):
        from config import loaded_env_files

        monkeypatch.chdir(tmp_path)
        assert loaded_env_files() == []
        assert Settings().llm_model == "gpt-4o"
