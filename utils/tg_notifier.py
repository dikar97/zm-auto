"""
本模块仅供学习与交流用途，请遵守当地法律法规，不得用于任何违反法律或第三方服务条款的场景。
作者不对任何因滥用本代码造成的后果承担责任。

Telegram Bot 通知模块：注册成功/失败时推送消息到指定 chat。
所有网络失败静默处理，绝不阻塞主注册流程。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from utils.log_masking import mask_with, MaskConfig

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org"


@dataclass
class TgConfig:
    """TG Bot 配置。"""
    enable: bool = False
    token: str = ""
    chat_id: str = ""
    mask_email: bool = True
    mask_password: bool = True


class TgNotifier:
    """Telegram Bot 通知器。所有错误静默吞掉，绝不向上抛。"""

    def __init__(self, config: TgConfig) -> None:
        self.config = config
        self._mask_cfg = MaskConfig(
            enable_email=config.mask_email,
            enable_apikey=True,
            enable_bearer=True,
            enable_cookie_token=True,
            enable_password=config.mask_password,
        )

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "TgNotifier | None":
        """从 config.json 顶层 tg_bot 段构造；enable=False 或缺字段返 None。"""
        tg = cfg.get("tg_bot") or {}
        if not isinstance(tg, dict):
            return None
        config = TgConfig(
            enable=bool(tg.get("enable", False)),
            token=str(tg.get("token", "") or "").strip(),
            chat_id=str(tg.get("chat_id", "") or "").strip(),
            mask_email=bool(tg.get("mask_email", True)),
            mask_password=bool(tg.get("mask_password", True)),
        )
        if not config.enable or not config.token or not config.chat_id:
            return None
        return cls(config)

    def _mask(self, text: object) -> object:
        return mask_with(text, self._mask_cfg)

    def send(self, text: str) -> bool:
        """发送原始文本。返 True=成功，False=失败/未启用。"""
        if not self.config.enable or not self.config.token or not self.config.chat_id:
            return False
        url = f"{_TG_API_BASE}/bot{self.config.token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={"chat_id": self.config.chat_id, "text": text, "disable_web_page_preview": True},
                timeout=10,
            )
            data = resp.json()
            return bool(data.get("ok"))
        except Exception as exc:
            logger.warning("TG 通知发送失败: %s", exc)
            return False

    def send_success(self, account: dict[str, Any]) -> bool:
        """注册成功通知。account 至少含 email/api_key 字段。"""
        email = self._mask(account.get("email", "")) or ""
        api_key = self._mask(account.get("api_key", "")) or ""
        user_id = account.get("user_id", "")
        proxy_name = account.get("proxy_name", "")
        elapsed = account.get("elapsed_sec", "")
        lines = [
            "✅ 注册成功",
            f"邮箱: {email}",
        ]
        if user_id:
            lines.append(f"用户 ID: {user_id}")
        if api_key:
            lines.append(f"API Key: {api_key}")
        if proxy_name:
            lines.append(f"代理: {proxy_name}")
        if elapsed != "":
            lines.append(f"耗时: {elapsed}s")
        return self.send("\n".join(lines))

    def send_failure(self, error: str, context: dict[str, Any] | None = None) -> bool:
        """注册失败通知。"""
        ctx = context or {}
        email = self._mask(ctx.get("email", "")) or ""
        proxy_name = ctx.get("proxy_name", "")
        index = ctx.get("index", "")
        lines = [
            "❌ 注册失败",
            f"错误: {error}",
        ]
        if email:
            lines.append(f"邮箱: {email}")
        if proxy_name:
            lines.append(f"代理: {proxy_name}")
        if index != "":
            lines.append(f"序号: {index}")
        return self.send("\n".join(lines))
