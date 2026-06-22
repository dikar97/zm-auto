"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。

订阅转 raw_proxy_pool 工具。

把常见的代理订阅内容转换为 ``register.py`` 的 ``raw_proxy_pool`` 可直接使用的
代理 URL 列表。纯 Python 实现（``fetch_subscription`` 按需懒加载 requests）。

支持三种输入格式（自动识别）::

    Clash YAML        含 ``proxies:`` 的 YAML（需 PyYAML，否则跳过该路径）
    base64 订阅        单段 base64，解码后是按行排列的节点 URI 列表
    纯文本/URI 列表    每行一个节点 URI 或 host:port 形式

设计约束:
    本项目走纯 HTTP（curl_cffi / requests），只能使用 http/https/socks 类型节点。
    vmess / ss / ssr / trojan / vless / hysteria 等需要 mihomo 等核心才能落地，
    这里一律跳过并计入 ``skipped`` 统计，由上层提示用户。
"""

from __future__ import annotations

import base64
import binascii

from utils.config import has_yaml
from utils.proxy_pool import parse_proxy_pool

# 本项目（纯 HTTP 客户端）能直接落地的节点类型
_SUPPORTED_SCHEMES = {"http", "https", "socks4", "socks5", "socks5h"}


def _try_base64_decode(text: str) -> str | None:
    """尝试把整段内容当作 base64 订阅解码；不像订阅则返回 ``None``。"""
    compact = "".join(text.split())  # 去掉所有空白（订阅常带换行）
    if not compact:
        return None
    pad = len(compact) % 4
    if pad:
        compact += "=" * (4 - pad)
    try:
        raw = base64.b64decode(compact, validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # 解码结果必须含节点 URI，否则认定不是 base64 订阅
    return decoded if "://" in decoded else None


def _extract_from_clash(text: str) -> tuple[list[str], dict[str, int]] | None:
    """解析 Clash YAML 的 ``proxies`` 段；非 Clash YAML 或缺 PyYAML 返回 ``None``。"""
    if not has_yaml():
        return None
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or "proxies" not in data:
        return None

    proxies = data.get("proxies") or []
    lines: list[str] = []
    skipped: dict[str, int] = {}
    for node in proxies:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type", "")).strip().lower()
        if ntype not in _SUPPORTED_SCHEMES:
            skipped[ntype or "unknown"] = skipped.get(ntype or "unknown", 0) + 1
            continue
        server = node.get("server")
        port = node.get("port")
        if not server or not port:
            continue
        user = node.get("username")
        pwd = node.get("password")
        if user and pwd:
            lines.append(f"{ntype}://{user}:{pwd}@{server}:{port}")
        else:
            lines.append(f"{ntype}://{server}:{port}")
    return parse_proxy_pool(lines), skipped


def _extract_from_uri_list(text: str) -> tuple[list[str], dict[str, int]]:
    """解析按行排列的节点 URI / host:port 列表，跳过非 http/socks 类型并计数。"""
    lines: list[str] = []
    skipped: dict[str, int] = {}
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        scheme = s.split("://", 1)[0].strip().lower() if "://" in s else ""
        if scheme and scheme not in _SUPPORTED_SCHEMES:
            skipped[scheme] = skipped.get(scheme, 0) + 1
            continue
        lines.append(s)
    return parse_proxy_pool(lines), skipped


def convert_subscription(content: str | None) -> tuple[list[str], dict[str, int]]:
    """把订阅内容转换为代理 URL 列表 + 跳过统计 ``{类型: 数量}``。

    自动识别 Clash YAML / base64 订阅 / 纯文本 URI 列表三种格式。
    """
    if not content:
        return [], {}
    text = content.strip()

    # 1. Clash YAML（含 proxies:）
    if "proxies:" in text:
        clash = _extract_from_clash(text)
        if clash is not None:
            return clash

    # 2. base64 订阅（解码成功则替换为解码后的 URI 列表）
    decoded = _try_base64_decode(text)
    if decoded is not None:
        text = decoded

    # 3. 纯文本 / URI 列表
    return _extract_from_uri_list(text)


def fetch_subscription(url: str, timeout: int = 30) -> str:
    """拉取订阅 URL 的原始内容（懒加载 requests，verify=False 与本项目一致）。"""
    import requests

    resp = requests.get(
        url,
        timeout=timeout,
        verify=False,
        headers={"User-Agent": "clash-verge/v1.0"},
    )
    resp.raise_for_status()
    return resp.text
