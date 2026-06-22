"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。

批量代理池解析工具。

支持把用户粘贴的一批"原始代理"行解析为标准代理 URL，便于直接喂给
``register.py`` 的代理轮询逻辑。纯 Python 实现，无任何外部依赖。

支持的单行格式（大小写不敏感的 scheme）::

    host:port                         -> http://host:port
    host:port:user:pass               -> http://user:pass@host:port
    scheme://host:port                -> scheme://host:port
    scheme://user:pass@host:port      -> scheme://user:pass@host:port
    scheme://host:port:user:pass      -> scheme://user:pass@host:port
    [2001:db8::1]:port                -> http://[2001:db8::1]:port  (IPv6)

设计原则:
    1. 无法识别的行返回 ``None``，由上层决定跳过（不抛异常阻断批量解析）
    2. 空行与 ``#`` 开头的注释行直接忽略
    3. 仅接受常见 scheme，拼写错误的 scheme 视为非法行剔除
"""

from __future__ import annotations

# 仅接受这些 scheme，避免把 "htp://" 之类拼写错误当成合法代理
_ALLOWED_SCHEMES = {"http", "https", "socks4", "socks5", "socks5h"}


def _is_port(value: str) -> bool:
    """端口必须是 1-65535 的纯数字。"""
    return value.isdigit() and 0 < int(value) <= 65535


def _split_host_port_auth(rest: str) -> tuple[str, str | None] | None:
    """解析不含 scheme、不含 ``@`` 的剩余部分。

    返回 ``(hostport, auth)``：``auth`` 为 ``"user:pass"`` 或 ``None``；
    无法解析时返回 ``None``。
    """
    # IPv6 带方括号：[addr]:port 或 [addr]:port:user:pass
    if rest.startswith("["):
        end = rest.find("]")
        if end == -1:
            return None
        host = rest[: end + 1]
        tail = rest[end + 1 :]
        if not tail.startswith(":"):
            return None
        seg = tail[1:].split(":")
        if len(seg) == 1 and _is_port(seg[0]):
            return f"{host}:{seg[0]}", None
        if len(seg) == 3 and _is_port(seg[0]) and seg[1] and seg[2]:
            return f"{host}:{seg[0]}", f"{seg[1]}:{seg[2]}"
        return None

    parts = rest.split(":")
    # host:port
    if len(parts) == 2 and parts[0] and _is_port(parts[1]):
        return rest, None
    # host:port:user:pass
    if len(parts) == 4 and parts[0] and _is_port(parts[1]) and parts[2] and parts[3]:
        host, port, user, pwd = parts
        return f"{host}:{port}", f"{user}:{pwd}"
    return None


def parse_proxy_line(line: str | None) -> str | None:
    """解析单行代理，返回标准代理 URL；非法/空行/注释行返回 ``None``。"""
    if line is None:
        return None
    s = line.strip()
    if not s or s.startswith("#"):
        return None

    scheme = "http"
    rest = s
    if "://" in s:
        scheme, rest = s.split("://", 1)
        scheme = scheme.strip().lower()
    if scheme not in _ALLOWED_SCHEMES or not rest:
        return None

    # 已是 user:pass@host:port 形式
    if "@" in rest:
        auth, _, hostport = rest.rpartition("@")
        if not auth or not hostport:
            return None
        parsed = _split_host_port_auth(hostport)
        # @ 后面必须是纯 host:port（不能再带内嵌 auth）
        if parsed is None or parsed[1] is not None:
            return None
        return f"{scheme}://{auth}@{hostport}"

    parsed = _split_host_port_auth(rest)
    if parsed is None:
        return None
    hostport, auth = parsed
    if auth:
        return f"{scheme}://{auth}@{hostport}"
    return f"{scheme}://{hostport}"


def parse_proxy_pool(text: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """批量解析多行代理。

    接受多行字符串（Web 粘贴框）或字符串列表（YAML/JSON 配置）。
    自动跳过空行/注释行/非法行，并按出现顺序去重。
    """
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        lines: list[str] = []
        for item in text:
            lines.extend(str(item).splitlines())
    else:
        lines = str(text).splitlines()

    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        proxy = parse_proxy_line(line)
        if proxy and proxy not in seen:
            seen.add(proxy)
            result.append(proxy)
    return result
