"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。

服务器磁盘清理状态查询。

供 server.py 的 /api/maintenance/cleanup_status 调用，
返回当前磁盘占用、清理阈值、脚本是否存在、能否执行等信息。
跨平台兼容：Windows 下 disk_used_percent 返回 None。
"""

import os
import platform
from typing import Any, Optional


def _to_int(value: Any, default: int) -> int:
    """安全转 int，失败回退默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_disk_usage_percent(target_path: str = "/") -> Optional[int]:
    """返回目标路径的磁盘占用百分比 (0-100)，失败或非 Linux 返回 None。"""
    if platform.system().lower() != "linux":
        return None
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        return None
    try:
        usage = statvfs(target_path)
    except OSError:
        return None
    total_blocks = int(getattr(usage, "f_blocks", 0) or 0)
    available_blocks = int(getattr(usage, "f_bavail", 0) or 0)
    if total_blocks <= 0:
        return None
    used_ratio = 1 - (available_blocks / total_blocks)
    return max(0, min(100, int(round(used_ratio * 100))))


def get_cleanup_status(base_dir: str) -> dict[str, Any]:
    """返回清理脚本状态字典，供前端展示。

    字段:
        platform: 当前操作系统
        is_linux: 是否 Linux（清理脚本只在 Linux 跑）
        script_path: scripts/server_disk_cleanup.sh 在项目中的路径
        script_exists: 脚本是否存在
        target_path: 清理目标磁盘（默认 "/"）
        app_dir: 应用根目录（清理目标文件所在）
        threshold_percent: 触发清理的占用阈值（默认 80）
        disk_used_percent: 当前占用百分比（Windows/非 Linux 为 None）
        can_run: 是否可执行清理（Linux + 脚本存在）
    """
    target_path = os.getenv("DISK_CLEANUP_TARGET_PATH", "/")
    threshold = _to_int(os.getenv("DISK_CLEANUP_THRESHOLD_PERCENT"), 80)
    app_dir = os.getenv("ZM_AUTO_APP_DIR", base_dir)
    script_path = os.path.join(base_dir, "scripts", "server_disk_cleanup.sh")
    is_linux = platform.system().lower() == "linux"
    disk_used_percent = get_disk_usage_percent(target_path)
    return {
        "platform": platform.system(),
        "is_linux": is_linux,
        "script_path": script_path,
        "script_exists": os.path.isfile(script_path),
        "target_path": target_path,
        "app_dir": app_dir,
        "threshold_percent": threshold,
        "disk_used_percent": disk_used_percent,
        "can_run": is_linux and os.path.isfile(script_path),
    }
