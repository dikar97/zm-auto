"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。
"""

from __future__ import annotations

import requests
import urllib3
from typing import Any

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Sub2APIImporter:
    """Import API keys into Sub2API."""

    def __init__(self, cfg: dict[str, Any]):
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        self.email = str(cfg.get("email", ""))
        self.password = str(cfg.get("password", ""))
        self.group_name = str(cfg.get("group_name", "auto"))
        self.concurrency = int(cfg.get("concurrency", 3))
        self.models = list(cfg.get("models", []))
        self.upstream_base_url = str(cfg.get("upstream_base_url", ""))
        self._token: str = ""
        self._group_id: int = 0
        self._session = requests.Session()

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    def login(self) -> None:
        r = self._session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=15,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Sub2API 登录失败: {data}")
        self._token = str(data.get("data", {}).get("access_token") or "")
        if not self._token:
            raise RuntimeError("Sub2API 登录未返回 access_token")

    @property
    def headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("Sub2API 未登录")
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    # ------------------------------------------------------------------ #
    # Group
    # ------------------------------------------------------------------ #
    def ensure_group(self) -> int:
        """Find the target group by name, create if missing. Returns group ID."""
        r = self._session.get(
            f"{self.base_url}/api/v1/admin/groups/all",
            headers=self.headers,
            timeout=15,
        )
        data = r.json()
        groups = data.get("data") or []
        if isinstance(groups, dict):
            groups = groups.get("groups") or groups.get("items") or []
        for g in groups:
            if str(g.get("name", "")).strip() == self.group_name:
                self._group_id = int(g.get("id", 0))
                return self._group_id

        # Create the group
        r = self._session.post(
            f"{self.base_url}/api/v1/admin/groups",
            headers=self.headers,
            json={"name": self.group_name, "description": "auto-registered accounts"},
            timeout=15,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建分组失败: {data}")
        self._group_id = int(data.get("data", {}).get("id", 0))
        return self._group_id

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    def import_account(self, api_key: str, name: str = "") -> dict[str, Any]:
        """Create an anthropic apikey account in the target group.

        Returns the created account data.
        """
        if not self._group_id:
            self.ensure_group()

        # Build model_mapping: each model maps to itself
        model_mapping = {model: model for model in self.models}

        payload = {
            "name": name or f"auto-{api_key[-6:]}",
            "platform": "anthropic",
            "type": "apikey",
            "credentials": {
                "api_key": api_key,
                "base_url": self.upstream_base_url,
                "model_mapping": model_mapping,
            },
            "concurrency": self.concurrency,
            "group_ids": [self._group_id],
        }

        r = self._session.post(
            f"{self.base_url}/api/v1/admin/accounts",
            headers=self.headers,
            json=payload,
            timeout=15,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建账号失败: {data}")
        return data.get("data") or {}

    # ------------------------------------------------------------------ #
    # Full flow
    # ------------------------------------------------------------------ #
    def import_key(self, api_key: str, name: str = "") -> dict[str, Any]:
        """Login → ensure group → create account. Returns account data."""
        if not self._token:
            self.login()
        if not self._group_id:
            self.ensure_group()
        return self.import_account(api_key, name)
