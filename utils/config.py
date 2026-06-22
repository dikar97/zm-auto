"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。

配置加载、合并与验证工具。

提供以下通用能力（不绑定具体业务 schema）：
- ``deep_update_config``: 递归合并默认值，实现配置文件无缝升级
- ``format_docker_url`` / ``is_in_docker``: Docker 容器内自动改写回环地址
- ``load_json_config``: 统一的 JSON 配置加载器（自动补齐缺失 key 并回写）
- ``validate_config``: 基础结构校验

设计原则:
    1. 通用工具不耦合具体业务字段，业务方自行传入 ``default_config``
    2. 不破坏 ``register.py`` 现有的模块级 ``load_config()`` 行为
    3. 配置文件损坏时降级为默认值，绝不抛异常阻断启动
    4. 自动回写仅补齐缺失 Key，不覆盖用户已设的值
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any


def is_in_docker() -> bool:
    """检测当前进程是否运行在 Docker 容器内。

    判据：容器内会存在 ``/.dockerenv`` 标记文件。
    Windows 开发机原生运行时返回 False（正确行为，无需改写回环地址）。
    """
    if os.path.exists("/.dockerenv"):
        return True
    # 兜底：某些容器编排不落 .dockerenv，看 cgroup 线索
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if "docker" in content or "containerd" in content:
            return True
    except OSError:
        pass
    return False


def format_docker_url(url: str) -> str:
    """Docker 容器内自动把回环地址改写为 ``host.docker.internal``。

    用途：注册代理、Sub2API 地址、邮箱服务地址若指向宿主机，
    容器内无法通过 127.0.0.1 访问，需改写为 Docker 桥接域名。

    非容器环境下原样返回。
    """
    if not url:
        return url
    if not is_in_docker():
        return url
    return (
        url.replace("127.0.0.1", "host.docker.internal")
        .replace("localhost", "host.docker.internal")
    )


def deep_update_config(
    default: dict[str, Any], user: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """递归合并默认值到用户配置。

    规则：
        - 遍历 ``default`` 的 key，若 ``user`` 缺失则补上默认值
        - 两边都是 dict 时递归合并
        - **不覆盖**用户已设的值，仅补齐缺失项

    Args:
        default: 默认配置模板
        user: 用户实际配置

    Returns:
        (merged_config, was_updated): 合并后的完整配置 / 是否补齐过缺失 key
    """
    was_updated = False
    result: dict[str, Any] = copy.deepcopy(user)
    for key, default_value in default.items():
        if key not in result:
            result[key] = copy.deepcopy(default_value)
            was_updated = True
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            sub_default = cast_dict(default_value)
            sub_user = cast_dict(result[key])
            merged, sub_updated = deep_update_config(sub_default, sub_user)
            if sub_updated:
                result[key] = merged
                was_updated = True
    return result, was_updated


def cast_dict(value: Any) -> dict[str, Any]:
    """把 Any 收窄为 dict[str, Any]（isinstance 已保证，仅为类型检查器服务）。"""
    return value  # type: ignore[return-value]


def load_json_config(
    config_path: Path,
    default_config: dict[str, Any],
    *,
    auto_save: bool = True,
) -> tuple[dict[str, Any], bool]:
    """加载 JSON 配置文件，自动补齐缺失的默认值。

    流程：
        1. 以默认配置为基底
        2. 若配置文件存在，读取用户值并递归合并
        3. 若有缺失 key 被补齐且 ``auto_save=True``，回写文件（保留用户原值）

    Args:
        config_path: 配置文件路径（如 ``config.json``）
        default_config: 默认配置字典
        auto_save: 补齐缺失 key 后是否自动回写文件

    Returns:
        (merged_config, was_updated)
    """
    base: dict[str, Any] = copy.deepcopy(default_config)

    if not config_path.exists():
        # 配置文件不存在视为"全新"，调用方决定是否从 example 拷贝
        return base, True

    try:
        raw = config_path.read_text(encoding="utf-8")
        saved_raw = json.loads(raw)
        if not isinstance(saved_raw, dict):
            # 顶层非 dict（损坏），降级为默认值
            return base, True
    except (json.JSONDecodeError, OSError):
        # 损坏的配置文件不阻断启动，降级为默认值
        return base, True

    saved: dict[str, Any] = saved_raw  # type: ignore[assignment]
    merged, was_updated = deep_update_config(default_config, saved)

    # 回写：仅当确实补齐了缺失 key
    if was_updated and auto_save:
        try:
            _ = config_path.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            # 回写失败不阻断启动（可能只读文件系统）
            pass

    return merged, was_updated


def validate_config(
    config: dict[str, Any], required_keys: list[str] | None = None
) -> list[str]:
    """基础结构校验，返回错误信息列表（空列表表示通过）。

    Args:
        config: 待校验的配置字典
        required_keys: 必须存在的顶层 key 列表；None 时仅做类型校验

    Returns:
        错误信息列表；空列表表示校验通过
    """
    errors: list[str] = []

    if required_keys:
        for key in required_keys:
            if key not in config:
                errors.append(f"缺少必需字段: {key}")

    # 已知字段的类型约束（与 DEFAULT_CONFIG 对齐）
    type_hints: dict[str, type] = {
        "mail": dict,
        "proxy": str,
        "register_proxies": list,
        "total": int,
        "threads": int,
        "captcha": dict,
        "api_key_name": str,
        "target_base": str,
        "target_api_version": str,
        "sub2api": dict,
    }
    for key, expected in type_hints.items():
        if key in config:
            val = config[key]
            # 兼容 proxy: "" 或 null
            if key == "proxy" and (val is None or val == ""):
                continue
            if not isinstance(val, expected):
                errors.append(
                    f"字段 '{key}' 类型错误: 期望 {expected.__name__}, "
                    f"实际 {type(val).__name__}"
                )

    return errors


def has_pydantic() -> bool:
    """是否可用 pydantic（用于上层决定是否启用增强校验）。"""
    try:
        import pydantic  # noqa: F401

        return True
    except ImportError:
        return False


def has_yaml() -> bool:
    """是否可用 PyYAML（用于上层决定是否启用 YAML 配置加载）。"""
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


def load_yaml_config(
    config_path: Path,
    default_config: dict[str, Any],
    *,
    auto_save: bool = False,
) -> tuple[dict[str, Any], bool] | None:
    """加载 YAML 配置文件，自动补齐缺失的默认值。

    与 ``load_json_config`` 对应的 YAML 版本。PyYAML 不可用时返 None，
    调用方可降级为 JSON 加载。

    YAML 默认 ``auto_save=False``：避免 safe_dump 覆盖用户精心维护的注释。
    用户需自行保证 YAML 文件字段完整（可借助 ``config.example.yaml`` 对照）。

    Args:
        config_path: 配置文件路径（如 ``config.yaml``）
        default_config: 默认配置字典
        auto_save: 补齐缺失 key 后是否自动回写文件（默认 False）

    Returns:
        (merged_config, was_updated)；PyYAML 不可用时返 None
    """
    try:
        import yaml
    except ImportError:
        return None

    base: dict[str, Any] = copy.deepcopy(default_config)

    if not config_path.exists():
        return base, True

    try:
        raw = config_path.read_text(encoding="utf-8")
        saved_raw = yaml.safe_load(raw)
        if not isinstance(saved_raw, dict):
            return base, True
    except (yaml.YAMLError, OSError):
        return base, True

    saved: dict[str, Any] = saved_raw  # type: ignore[assignment]
    merged, was_updated = deep_update_config(default_config, saved)

    if was_updated and auto_save:
        try:
            _ = config_path.write_text(
                yaml.safe_dump(
                    merged,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    return merged, was_updated


def load_config_file(
    config_path: Path,
    default_config: dict[str, Any],
    *,
    auto_save: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    """根据文件扩展名自动选择 YAML/JSON 加载器。

    优先级：.yaml / .yml → YAML 加载；其他 → JSON 加载。
    ``auto_save=None`` 时：JSON 默认 True（无缝升级），YAML 默认 False（保留注释）。

    Args:
        config_path: 配置文件路径
        default_config: 默认配置字典
        auto_save: 显式控制是否回写；None 时按格式取默认值

    Returns:
        (merged_config, was_updated)
    """
    suffix = config_path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        save_flag = auto_save if auto_save is not None else False
        result = load_yaml_config(config_path, default_config, auto_save=save_flag)
        if result is not None:
            return result
        print(
            f"[警告] 检测到 {config_path.name} 但 PyYAML 未安装，"
            "已降级为默认配置（captcha/sub2api 等配置全部失效，注册大概率失败）。"
            "请执行 pip install PyYAML 后重试。",
            file=sys.stderr,
        )
        return copy.deepcopy(default_config), True
    save_flag = auto_save if auto_save is not None else True
    return load_json_config(config_path, default_config, auto_save=save_flag)


def find_config_file(
    base_dir: Path, base_name: str = "config"
) -> Path | None:
    """按优先级查找配置文件：base_name.yaml > .yml > .json。

    用于支持 YAML 配置的同时保持对现有 JSON 用户的向后兼容。
    """
    for ext in (".yaml", ".yml", ".json"):
        path = base_dir / f"{base_name}{ext}"
        if path.exists():
            return path
    return None
