"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。

日志脱敏工具。

对输出到终端 / SSE / 文件的日志文本进行自动脱敏，防止敏感信息泄露：
- 邮箱地址：foo@example.com → f**@example.com（保留首字符和域名）
- API Key：sk-abcdef123456 → sk-a***（保留前缀）
- Bearer Token：Authorization: Bearer xxx → Bearer ***
- Cookie ctoken：ctoken=abc123 → ctoken=***
- 密码字段：password: secret / "password":"secret" → password: ***

用法：
    from utils.log_masking import mask
    safe_text = mask(raw_log_line)

    # 或自定义启用类别
    from utils.log_masking import MaskConfig, mask_with
    cfg = MaskConfig(enable_email=False)  # 关闭邮箱脱敏
    safe_text = mask_with(raw_log_line, cfg)

设计：
    1. 所有正则预编译，热路径零编译开销
    2. 无副作用：输入 None / 非 str 原样返回
    3. 可通过 MaskConfig 细粒度控制
    4. 模块级默认实例 _default_config，热路径只需 mask(text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---- 正则模式（预编译）------------------------------------------------------ #

# 邮箱：保留首字符 + 域名。user@example.com → u**@example.com
_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+-])[a-zA-Z0-9._%+-]*@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")

# API Key：sk- / key- / ghp_ 等常见前缀，保留前缀 + 首字符
# 例：sk-abcdef123456 → sk-a***
_APIKEY_RE = re.compile(
    r"\b((?:sk|key|ghp|github_pat|glpat|xoxb|xoxp|aiza)[-_]?[A-Za-z0-9])([A-Za-z0-9]{4,})\b"
)

# Bearer Token：Authorization: Bearer xxx → Bearer ***
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-+=/]+)", re.IGNORECASE)

# Cookie 中的 ctoken / session 等：ctoken=xxx → ctoken=***
_COOKIE_TOKEN_RE = re.compile(
    r"\b(ctoken|session|sid|token|access_token|refresh_token)(=)([^;\s\"']+)",
    re.IGNORECASE,
)

# JSON/表单中的密码字段："password":"xxx" / password=xxx / "pwd":"xxx"
# _PWD_KV_RE: 匹配 JSON 键值对 "password":"value"，group(1)=键+冒号+开引号, group(2)=闭引号
_PWD_KV_RE = re.compile(
    r'("(?:password|passwd|pwd|secret|api_key|apikey)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)
# _PWD_ASSIGN_RE: 匹配赋值 password=secret / password: secret
_PWD_ASSIGN_RE = re.compile(
    r"\b(password|passwd|pwd|secret|api_key|apikey)(\s*[=:]\s*)\S+",
    re.IGNORECASE,
)

# Sub2API 邮箱/密码 JSON："email":"xxx@xxx" 中的邮箱由 _EMAIL_RE 覆盖


@dataclass
class MaskConfig:
    """脱敏开关配置。

    所有字段默认 True（安全优先）；按需关闭某些类别。
    """

    enable_email: bool = True
    enable_apikey: bool = True
    enable_bearer: bool = True
    enable_cookie_token: bool = True
    enable_password: bool = True


# 模块级默认配置（全开）
_default_config = MaskConfig()


# ---- 单项脱敏函数 ---------------------------------------------------------- #


def _mask_email(text: str) -> str:
    """邮箱脱敏：user@example.com → u**@example.com。"""

    def _replace(m: re.Match[str]) -> str:
        first_char = m.group(1)
        domain = m.group(2)
        return f"{first_char}**@{domain}"

    return _EMAIL_RE.sub(_replace, text)


def _mask_apikey(text: str) -> str:
    """API Key 脱敏：sk-abcdef123456 → sk-a***。"""

    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        return f"{prefix}***"

    return _APIKEY_RE.sub(_replace, text)


def _mask_bearer(text: str) -> str:
    """Bearer Token 脱敏：Authorization: Bearer xxx → Bearer ***。"""
    return _BEARER_RE.sub(r"\1***", text)


def _mask_cookie_token(text: str) -> str:
    """Cookie/token 字段脱敏：ctoken=xxx → ctoken=***。"""
    return _COOKIE_TOKEN_RE.sub(r"\1\2***", text)


def _mask_password(text: str) -> str:
    """密码字段脱敏：password: secret / "password":"secret" → password: ***。"""

    # _PWD_KV_RE: group(1)="password":", group(2)=" → 替换为 \1***\2
    text = _PWD_KV_RE.sub(r"\1***\2", text)

    # _PWD_ASSIGN_RE: group(1)=password, group(2)==/ : → 替换为 \1\2***
    text = _PWD_ASSIGN_RE.sub(r"\1\2***", text)
    return text


# ---- 公共 API -------------------------------------------------------------- #


def mask_with(text: object, cfg: MaskConfig) -> object:
    """对文本应用指定配置的脱敏。

    Args:
        text: 待脱敏的文本（非 str 原样返回，便于在 log() 中安全调用）
        cfg: 脱敏开关配置

    Returns:
        脱敏后的文本（类型与输入一致）
    """
    if not isinstance(text, str):
        return text

    result = text
    if cfg.enable_email:
        result = _mask_email(result)
    if cfg.enable_apikey:
        result = _mask_apikey(result)
    if cfg.enable_bearer:
        result = _mask_bearer(result)
    if cfg.enable_cookie_token:
        result = _mask_cookie_token(result)
    if cfg.enable_password:
        result = _mask_password(result)
    return result


def mask(text: object) -> object:
    """默认脱敏（全开）。热路径首选。

    Args:
        text: 待脱敏的文本（非 str 原样返回）

    Returns:
        脱敏后的文本
    """
    return mask_with(text, _default_config)


def set_default_config(cfg: MaskConfig) -> None:
    """更新模块级默认配置（影响后续 mask() 调用）。

    用于从 config.json 的 enable_email_masking 字段切换开关。
    """
    global _default_config
    _default_config = cfg
