"""utils/config.py 单元测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from utils import config as cfg_mod


# ----------------------------- TestIsInDocker -----------------------------
class TestIsInDocker:
    def test_dockerenv_exists_returns_true(self) -> None:
        def fake_exists(path: str) -> bool:
            return path == "/.dockerenv"
        with patch.object(cfg_mod.os.path, "exists", side_effect=fake_exists):
            assert cfg_mod.is_in_docker() is True

    def test_dockerenv_absent_cgroup_absent_returns_false(self) -> None:
        with patch.object(cfg_mod.os.path, "exists", return_value=False):
            with patch("builtins.open", side_effect=OSError("no file")):
                assert cfg_mod.is_in_docker() is False


# ----------------------------- TestFormatDockerUrl -----------------------------
class TestFormatDockerUrl:
    def test_empty_returns_empty(self) -> None:
        assert cfg_mod.format_docker_url("") == ""

    def test_not_in_docker_passthrough(self) -> None:
        with patch.object(cfg_mod, "is_in_docker", return_value=False):
            assert cfg_mod.format_docker_url("http://127.0.0.1:7890") == "http://127.0.0.1:7890"
            assert cfg_mod.format_docker_url("http://localhost:8080") == "http://localhost:8080"
            assert cfg_mod.format_docker_url("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"

    def test_in_docker_rewrites_127(self) -> None:
        with patch.object(cfg_mod, "is_in_docker", return_value=True):
            assert cfg_mod.format_docker_url("http://127.0.0.1:7890") == "http://host.docker.internal:7890"

    def test_in_docker_rewrites_localhost(self) -> None:
        with patch.object(cfg_mod, "is_in_docker", return_value=True):
            assert cfg_mod.format_docker_url("http://localhost:8080") == "http://host.docker.internal:8080"

    def test_in_docker_keeps_external(self) -> None:
        with patch.object(cfg_mod, "is_in_docker", return_value=True):
            assert cfg_mod.format_docker_url("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"

    def test_in_docker_rewrites_socks5_loopback(self) -> None:
        with patch.object(cfg_mod, "is_in_docker", return_value=True):
            assert cfg_mod.format_docker_url("socks5://127.0.0.1:1080") == "socks5://host.docker.internal:1080"


# ----------------------------- TestDeepUpdateConfig -----------------------------
class TestDeepUpdateConfig:
    def test_empty_default_no_change(self) -> None:
        merged, updated = cfg_mod.deep_update_config({}, {"a": 1})
        assert merged == {"a": 1}
        assert updated is False

    def test_empty_user_fills_all(self) -> None:
        merged, updated = cfg_mod.deep_update_config({"a": 1, "b": 2}, {})
        assert merged == {"a": 1, "b": 2}
        assert updated is True

    def test_user_value_preserved(self) -> None:
        merged, updated = cfg_mod.deep_update_config({"a": 1}, {"a": 99})
        assert merged == {"a": 99}
        assert updated is False

    def test_nested_recursive_fill(self) -> None:
        default = {"mail": {"proxy": "", "domains": ["a.com"]}}
        user = {"mail": {"proxy": "http://x"}}
        merged, updated = cfg_mod.deep_update_config(default, user)
        assert merged == {"mail": {"proxy": "http://x", "domains": ["a.com"]}}
        assert updated is True

    def test_nested_user_dict_preserved(self) -> None:
        default = {"mail": {"a": 1, "b": 2}}
        user = {"mail": {"a": 99}}
        merged, updated = cfg_mod.deep_update_config(default, user)
        assert merged == {"mail": {"a": 99, "b": 2}}
        assert updated is True

    def test_no_update_when_all_present(self) -> None:
        default = {"a": 1, "b": {"c": 2}}
        user = {"a": 1, "b": {"c": 2}}
        merged, updated = cfg_mod.deep_update_config(default, user)
        assert merged == {"a": 1, "b": {"c": 2}}
        assert updated is False

    def test_extra_user_keys_kept(self) -> None:
        merged, _ = cfg_mod.deep_update_config({"a": 1}, {"a": 1, "extra": "x"})
        assert merged == {"a": 1, "extra": "x"}


# ----------------------------- TestCastDict -----------------------------
class TestCastDict:
    def test_dict_passthrough(self) -> None:
        assert cfg_mod.cast_dict({"a": 1}) == {"a": 1}

    def test_value_returned_as_is(self) -> None:
        assert cfg_mod.cast_dict("hello") == "hello"
        assert cfg_mod.cast_dict(123) == 123
        assert cfg_mod.cast_dict(None) is None


# ----------------------------- TestLoadJsonConfig -----------------------------
class TestLoadJsonConfig:
    def test_file_not_exist_returns_default_and_updated(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.json"
        cfg, updated = cfg_mod.load_json_config(path, {"a": 1}, auto_save=False)
        assert cfg == {"a": 1}
        assert updated is True

    def test_file_not_exist_no_auto_save_no_write(self, tmp_path: Path) -> None:
        path = tmp_path / "new.json"
        cfg_mod.load_json_config(path, {"a": 1}, auto_save=False)
        assert not path.exists()

    def test_file_not_exist_auto_save_does_not_create(self, tmp_path: Path) -> None:
        path = tmp_path / "new.json"
        cfg, updated = cfg_mod.load_json_config(path, {"a": 1}, auto_save=True)
        assert cfg == {"a": 1}
        assert updated is True
        assert not path.exists()

    def test_merges_missing_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"a": 1}), encoding="utf-8")
        cfg, updated = cfg_mod.load_json_config(path, {"a": 0, "b": 2}, auto_save=False)
        assert cfg == {"a": 1, "b": 2}
        assert updated is True

    def test_no_update_when_complete(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
        cfg, updated = cfg_mod.load_json_config(path, {"a": 0, "b": 0}, auto_save=False)
        assert cfg == {"a": 1, "b": 2}
        assert updated is False

    def test_corrupted_file_falls_back_to_default(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        cfg, updated = cfg_mod.load_json_config(path, {"a": 1}, auto_save=False)
        assert cfg == {"a": 1}
        assert updated is True

    def test_non_dict_json_falls_back(self, tmp_path: Path) -> None:
        path = tmp_path / "arr.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        cfg, updated = cfg_mod.load_json_config(path, {"a": 1}, auto_save=False)
        assert cfg == {"a": 1}
        assert updated is True

    def test_auto_save_writes_back(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"a": 1}), encoding="utf-8")
        cfg_mod.load_json_config(path, {"a": 0, "b": 2, "c": 3}, auto_save=True)
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written == {"a": 1, "b": 2, "c": 3}


# ----------------------------- TestValidateConfig -----------------------------
class TestValidateConfig:
    def _full_config(self) -> dict[str, Any]:
        return {
            "mail": {"providers": []},
            "proxy": "",
            "register_proxies": [],
            "total": 5,
            "threads": 2,
            "captcha": {"api_key": "k"},
            "api_key_name": "auto",
            "target_base": "https://x.com",
            "target_api_version": "v1",
            "sub2api": {"base_url": "https://s.com"},
        }

    def test_complete_config_passes(self) -> None:
        assert cfg_mod.validate_config(self._full_config()) == []

    def test_missing_required_key_reported(self) -> None:
        errors = cfg_mod.validate_config({}, required_keys=["mail", "captcha"])
        assert any("mail" in e for e in errors)
        assert any("captcha" in e for e in errors)

    def test_wrong_type_reported(self) -> None:
        cfg = self._full_config()
        cfg["total"] = "five"
        errors = cfg_mod.validate_config(cfg)
        assert any("total" in e for e in errors)

    def test_proxy_none_or_empty_allowed(self) -> None:
        cfg = self._full_config()
        cfg["proxy"] = ""
        assert cfg_mod.validate_config(cfg) == []
        cfg["proxy"] = None
        assert cfg_mod.validate_config(cfg) == []

    def test_custom_required_keys(self) -> None:
        errors = cfg_mod.validate_config({}, required_keys=["foo"])
        assert any("foo" in e for e in errors)


# ----------------------------- TestHasPydantic -----------------------------
class TestHasPydantic:
    def test_returns_bool(self) -> None:
        assert isinstance(cfg_mod.has_pydantic(), bool)

    def test_installed(self) -> None:
        assert cfg_mod.has_pydantic() is True


# ----------------------------- TestHasYaml -----------------------------
class TestHasYaml:
    def test_returns_bool(self) -> None:
        assert isinstance(cfg_mod.has_yaml(), bool)

    def test_installed(self) -> None:
        assert cfg_mod.has_yaml() is True


# ----------------------------- TestLoadYamlConfig -----------------------------
class TestLoadYamlConfig:
    def test_no_pyyaml_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "yaml", None)
        result = cfg_mod.load_yaml_config(Path("nonexistent.yaml"), {"a": 1})
        assert result is None

    def test_file_not_exist_returns_default_updated(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        result = cfg_mod.load_yaml_config(path, {"a": 1, "b": 2})
        assert result is not None
        merged, updated = result
        assert merged == {"a": 1, "b": 2}
        assert updated is True

    def test_normal_load_merges_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("a: 99\n", encoding="utf-8")
        result = cfg_mod.load_yaml_config(path, {"a": 1, "b": 2})
        assert result is not None
        merged, updated = result
        assert merged == {"a": 99, "b": 2}
        assert updated is True

    def test_full_config_no_update(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("a: 1\nb: 2\n", encoding="utf-8")
        result = cfg_mod.load_yaml_config(path, {"a": 1, "b": 2})
        assert result is not None
        merged, updated = result
        assert merged == {"a": 1, "b": 2}
        assert updated is False

    def test_corrupt_yaml_fallback(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(": : invalid\n  - broken", encoding="utf-8")
        result = cfg_mod.load_yaml_config(path, {"a": 1})
        assert result is not None
        merged, updated = result
        assert merged == {"a": 1}
        assert updated is True

    def test_non_dict_top_level_fallback(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        result = cfg_mod.load_yaml_config(path, {"a": 1})
        assert result is not None
        merged, updated = result
        assert merged == {"a": 1}
        assert updated is True

    def test_auto_save_default_false(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        original = "a: 99\n"
        path.write_text(original, encoding="utf-8")
        result = cfg_mod.load_yaml_config(path, {"a": 1, "b": 2})
        assert result is not None
        assert path.read_text(encoding="utf-8") == original

    def test_auto_save_true_writes_back(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("a: 99\n", encoding="utf-8")
        result = cfg_mod.load_yaml_config(path, {"a": 1, "b": 2}, auto_save=True)
        assert result is not None
        content = path.read_text(encoding="utf-8")
        assert "b: 2" in content


# ----------------------------- TestLoadConfigFile -----------------------------
class TestLoadConfigFile:
    def test_yaml_suffix_dispatches_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("total: 5\n", encoding="utf-8")
        merged, _ = cfg_mod.load_config_file(path, {"total": 1, "proxy": ""})
        assert merged["total"] == 5

    def test_yml_suffix_dispatches_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yml"
        path.write_text("total: 7\n", encoding="utf-8")
        merged, _ = cfg_mod.load_config_file(path, {"total": 1})
        assert merged["total"] == 7

    def test_json_suffix_dispatches_json(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text('{"total": 3}', encoding="utf-8")
        merged, _ = cfg_mod.load_config_file(path, {"total": 1})
        assert merged["total"] == 3

    def test_yaml_no_pyyaml_fallback_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setitem(sys.modules, "yaml", None)
        path = tmp_path / "config.yaml"
        path.write_text("total: 5\n", encoding="utf-8")
        merged, updated = cfg_mod.load_config_file(path, {"total": 1, "proxy": ""})
        assert merged == {"total": 1, "proxy": ""}
        assert updated is True


# ----------------------------- TestFindConfigFile -----------------------------
class TestFindConfigFile:
    def test_yaml_priority(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("x", encoding="utf-8")
        (tmp_path / "config.yml").write_text("x", encoding="utf-8")
        (tmp_path / "config.json").write_text("x", encoding="utf-8")
        found = cfg_mod.find_config_file(tmp_path)
        assert found is not None
        assert found.name == "config.yaml"

    def test_yml_when_no_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "config.yml").write_text("x", encoding="utf-8")
        (tmp_path / "config.json").write_text("x", encoding="utf-8")
        found = cfg_mod.find_config_file(tmp_path)
        assert found is not None
        assert found.name == "config.yml"

    def test_json_when_no_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_text("x", encoding="utf-8")
        found = cfg_mod.find_config_file(tmp_path)
        assert found is not None
        assert found.name == "config.json"

    def test_none_when_no_config(self, tmp_path: Path) -> None:
        found = cfg_mod.find_config_file(tmp_path)
        assert found is None

    def test_custom_base_name(self, tmp_path: Path) -> None:
        (tmp_path / "settings.yaml").write_text("x", encoding="utf-8")
        found = cfg_mod.find_config_file(tmp_path, "settings")
        assert found is not None
        assert found.name == "settings.yaml"
