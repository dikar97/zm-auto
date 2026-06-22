"""sub2api_importer 单元测试。

只测试不依赖网络的部分：
- Sub2APIImporter 构造（字段默认值）
- headers property（未登录报错）
- _next_proxy_id 轮询
- login / ensure_group / import_account / import_key（monkeypatch requests.Session）
"""

from __future__ import annotations

from typing import Any

import pytest

import sub2api_importer


# --------------------------------------------------------------------------- #
# 构造
# --------------------------------------------------------------------------- #
class TestConstruction:
    def test_full_config(self) -> None:
        cfg = {
            "base_url": "https://sub2api.example.com",
            "email": "admin@example.com",
            "password": "secret",
            "group_name": "test-group",
            "concurrency": 5,
            "models": ["model-a", "model-b"],
            "upstream_base_url": "https://upstream.example.com",
            "proxy_ids": [1, 2, 3],
        }
        importer = sub2api_importer.Sub2APIImporter(cfg)
        assert importer.base_url == "https://sub2api.example.com"
        assert importer.email == "admin@example.com"
        assert importer.password == "secret"
        assert importer.group_name == "test-group"
        assert importer.concurrency == 5
        assert importer.models == ["model-a", "model-b"]
        assert importer.upstream_base_url == "https://upstream.example.com"
        assert importer.proxy_ids == [1, 2, 3]

    def test_defaults_for_optional_fields(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://sub2api.example.com",
                "email": "admin@example.com",
                "password": "secret",
            }
        )
        # 默认 group_name='auto'、concurrency=3、models=[]、proxy_ids=[]
        assert importer.group_name == "auto"
        assert importer.concurrency == 3
        assert importer.models == []
        assert importer.proxy_ids == []

    def test_token_initially_empty(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {"base_url": "https://x", "email": "a", "password": "b"}
        )
        assert importer._token == ""
        assert importer._group_id == 0
        assert importer._proxy_index == 0


# --------------------------------------------------------------------------- #
# headers property
# --------------------------------------------------------------------------- #
class TestHeaders:
    def test_unauthenticated_raises(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {"base_url": "https://x", "email": "a", "password": "b"}
        )
        with pytest.raises(RuntimeError):
            _ = importer.headers

    def test_authenticated_returns_bearer(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {"base_url": "https://x", "email": "a", "password": "b"}
        )
        importer._token = "MY_TOKEN"
        headers = importer.headers
        assert headers["Authorization"] == "Bearer MY_TOKEN"
        assert headers["Content-Type"] == "application/json"


# --------------------------------------------------------------------------- #
# _next_proxy_id
# --------------------------------------------------------------------------- #
class TestNextProxyId:
    def test_no_proxy_ids(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {"base_url": "https://x", "email": "a", "password": "b"}
        )
        assert importer._next_proxy_id() is None

    def test_round_robin(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://x",
                "email": "a",
                "password": "b",
                "proxy_ids": [10, 20, 30],
            }
        )
        sequence = [importer._next_proxy_id() for _ in range(6)]
        assert sequence == [10, 20, 30, 10, 20, 30]

    def test_single_proxy_id(self) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://x",
                "email": "a",
                "password": "b",
                "proxy_ids": [42],
            }
        )
        assert [importer._next_proxy_id() for _ in range(3)] == [42, 42, 42]


# --------------------------------------------------------------------------- #
# login（mock requests.Session.post）
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class TestLogin:
    @staticmethod
    def _make_importer() -> sub2api_importer.Sub2APIImporter:
        return sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://sub2api.example.com",
                "email": "admin@example.com",
                "password": "secret",
            }
        )

    def test_success_sets_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            assert url == "https://sub2api.example.com/api/v1/auth/login"
            assert kw["json"]["email"] == "admin@example.com"
            return _FakeResp({"code": 0, "data": {"access_token": "TKN"}})

        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        importer.login()
        assert importer._token == "TKN"

    def test_nonzero_code_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp({"code": 401, "message": "invalid credentials"})

        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        with pytest.raises(RuntimeError):
            importer.login()

    def test_missing_access_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp({"code": 0, "data": {}})

        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        with pytest.raises(RuntimeError):
            importer.login()


# --------------------------------------------------------------------------- #
# ensure_group（mock Session.get + post）
# --------------------------------------------------------------------------- #
class TestEnsureGroup:
    @staticmethod
    def _make_importer() -> sub2api_importer.Sub2APIImporter:
        imp = sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://sub2api.example.com",
                "email": "admin@example.com",
                "password": "secret",
                "group_name": "zm-group",
            }
        )
        imp._token = "PRE_AUTH"
        return imp

    def test_finds_existing_group_in_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()

        def fake_get(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp(
                {
                    "code": 0,
                    "data": [
                        {"id": 7, "name": "zm-group"},
                        {"id": 8, "name": "other"},
                    ],
                }
            )

        monkeypatch.setattr(sub2api_importer.requests.Session, "get", fake_get)
        importer.ensure_group()
        assert importer._group_id == 7

    def test_finds_existing_group_in_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()

        def fake_get(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp(
                {
                    "code": 0,
                    "data": {"groups": [{"id": 11, "name": "zm-group"}]},
                }
            )

        monkeypatch.setattr(sub2api_importer.requests.Session, "get", fake_get)
        importer.ensure_group()
        assert importer._group_id == 11

    def test_creates_when_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()
        get_called = {"count": 0}

        def fake_get(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            get_called["count"] += 1
            return _FakeResp(
                {
                    "code": 0,
                    "data": [{"id": 8, "name": "other"}],
                }
            )

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            assert kw["json"]["name"] == "zm-group"
            return _FakeResp({"code": 0, "data": {"id": 99, "name": "zm-group"}})

        monkeypatch.setattr(sub2api_importer.requests.Session, "get", fake_get)
        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        importer.ensure_group()
        assert importer._group_id == 99
        assert get_called["count"] == 1

    def test_create_failed_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = self._make_importer()

        def fake_get(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp({"code": 0, "data": []})

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp({"code": 500, "message": "db error"})

        monkeypatch.setattr(sub2api_importer.requests.Session, "get", fake_get)
        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        with pytest.raises(RuntimeError):
            importer.ensure_group()


# --------------------------------------------------------------------------- #
# import_key（完整流程 mock）
# --------------------------------------------------------------------------- #
class TestImportKey:
    def test_full_flow_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://sub2api.example.com",
                "email": "admin@example.com",
                "password": "secret",
                "group_name": "zm-group",
                "models": ["gpt-4"],
                "upstream_base_url": "https://upstream.example.com",
            }
        )

        post_calls: list[tuple[str, str]] = []

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            if "/auth/login" in url:
                post_calls.append(("login", url))
                return _FakeResp({"code": 0, "data": {"access_token": "TKN"}})
            if "/admin/groups" in url:
                post_calls.append(("create_group", url))
                return _FakeResp({"code": 0, "data": {"id": 5, "name": "zm-group"}})
            if "/admin/accounts" in url:
                post_calls.append(("import_account", url))
                payload = kw["json"]
                assert payload["platform"] == "anthropic"
                assert payload["type"] == "apikey"
                assert payload["credentials"]["api_key"] == "sk-test-key"
                assert payload["credentials"]["base_url"] == "https://upstream.example.com"
                return _FakeResp({"code": 0, "data": {"id": 101}})
            raise AssertionError(f"unexpected POST {url}")

        def fake_get(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            if "/admin/groups/all" in url:
                return _FakeResp({"code": 0, "data": []})
            raise AssertionError(f"unexpected GET {url}")

        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        monkeypatch.setattr(sub2api_importer.requests.Session, "get", fake_get)

        result = importer.import_key("sk-test-key")
        labels = [step for step, _ in post_calls]
        assert labels == ["login", "create_group", "import_account"]
        assert isinstance(result, dict)

    def test_import_account_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        importer = sub2api_importer.Sub2APIImporter(
            {
                "base_url": "https://sub2api.example.com",
                "email": "admin@example.com",
                "password": "secret",
                "group_name": "zm-group",
                "models": [],
                "upstream_base_url": "https://upstream.example.com",
            }
        )

        def fake_post(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            if "/auth/login" in url:
                return _FakeResp({"code": 0, "data": {"access_token": "TKN"}})
            if "/admin/groups" in url:
                return _FakeResp({"code": 0, "data": {"id": 5, "name": "zm-group"}})
            if "/admin/accounts" in url:
                return _FakeResp({"code": 1001, "message": "duplicate"})
            raise AssertionError(f"unexpected POST {url}")

        def fake_get(self_sess: Any, url: str, **kw: Any) -> _FakeResp:
            return _FakeResp({"code": 0, "data": []})

        monkeypatch.setattr(sub2api_importer.requests.Session, "post", fake_post)
        monkeypatch.setattr(sub2api_importer.requests.Session, "get", fake_get)

        with pytest.raises(RuntimeError, match="duplicate"):
            importer.import_key("sk-fail-key")
