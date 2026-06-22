#!/usr/bin/env bash
# ⚠️ DISCLAIMER: zm-auto 磁盘清理脚本，仅供学习研究，使用者自行承担所有后果。
#
# 阈值触发：磁盘占用超过 ${DISK_CLEANUP_THRESHOLD_PERCENT}% 才清理（默认 80）。
# 清理目标：超大日志（裁掉最早 30%）、过期日志/缓存文件（按最旧排序删 30%）、Python __pycache__。
# 安全保证：保留最少 MIN_KEEP_LINES 行日志，避免清过头；不删用户数据（accounts.json/config.json）。
#
# 用法:
#   ./scripts/server_disk_cleanup.sh              # 阈值检查模式（占用 <80% 跳过）
#   ./scripts/server_disk_cleanup.sh --force      # 强制清理（无视阈值，用于应急）
#
# 环境变量（可覆盖）:
#   DISK_CLEANUP_THRESHOLD_PERCENT=80              触发阈值
#   DISK_CLEANUP_TARGET_PATH=/                     检查的挂载点
#   ZM_AUTO_APP_DIR=/opt/zm-auto                   应用根目录
#   DISK_CLEANUP_KEEP_LOG_LINES=2000               大日志保留最小行数
#   DISK_CLEANUP_MAX_LOG_BYTES=5242880             大日志阈值（5MB）
#   DISK_CLEANUP_DELETE_AFTER_DAYS=7               过期文件判定（7 天前）
#   DISK_CLEANUP_TRIM_OLDEST_PERCENT=30            大日志裁剪比例
#   DISK_CLEANUP_DELETE_OLDEST_PERCENT=30          过期文件删除比例
#   DISK_CLEANUP_MIN_KEEP_LINES=500                绝对最少保留行数

set -euo pipefail

THRESHOLD_PERCENT="${DISK_CLEANUP_THRESHOLD_PERCENT:-80}"
TARGET_PATH="${DISK_CLEANUP_TARGET_PATH:-/}"
APP_DIR="${ZM_AUTO_APP_DIR:-/opt/zm-auto}"
KEEP_LOG_LINES="${DISK_CLEANUP_KEEP_LOG_LINES:-2000}"
MAX_LOG_BYTES="${DISK_CLEANUP_MAX_LOG_BYTES:-5242880}"
DELETE_AFTER_DAYS="${DISK_CLEANUP_DELETE_AFTER_DAYS:-7}"
TRIM_OLDEST_PERCENT="${DISK_CLEANUP_TRIM_OLDEST_PERCENT:-30}"
DELETE_OLDEST_PERCENT="${DISK_CLEANUP_DELETE_OLDEST_PERCENT:-30}"
MIN_KEEP_LINES="${DISK_CLEANUP_MIN_KEEP_LINES:-500}"
FORCE_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE_RUN=1
      shift
      ;;
    *)
      shift
      ;;
  esac
done

log() {
  printf '[zm-auto-disk-cleanup] %s\n' "$*"
}

clamp_percent() {
  local value="$1"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo 30
    return
  fi
  if (( value < 1 )); then
    echo 1
  elif (( value > 90 )); then
    echo 90
  else
    echo "$value"
  fi
}

disk_usage_percent() {
  df -P "$TARGET_PATH" | awk 'NR==2 { gsub("%", "", $5); print $5 }'
}

trim_large_log() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    return 0
  fi

  local size
  size=$(stat -c %s "$file" 2>/dev/null || echo 0)
  if (( size <= MAX_LOG_BYTES )); then
    return 0
  fi

  local total_lines trim_percent remove_lines keep_from
  total_lines=$(wc -l < "$file" 2>/dev/null || echo 0)
  trim_percent=$(clamp_percent "$TRIM_OLDEST_PERCENT")
  remove_lines=$(( total_lines * trim_percent / 100 ))
  if (( remove_lines <= 0 )); then
    remove_lines=1
  fi
  if (( total_lines - remove_lines < MIN_KEEP_LINES )); then
    remove_lines=$(( total_lines - MIN_KEEP_LINES ))
  fi
  if (( remove_lines <= 0 )); then
    remove_lines=$(( total_lines > KEEP_LOG_LINES ? total_lines - KEEP_LOG_LINES : 0 ))
  fi

  local tmp
  tmp=$(mktemp)
  if (( remove_lines > 0 )); then
    keep_from=$(( remove_lines + 1 ))
    tail -n +"$keep_from" "$file" > "$tmp" || true
  else
    tail -n "$KEEP_LOG_LINES" "$file" > "$tmp" || true
  fi
  cat "$tmp" > "$file"
  rm -f "$tmp"
  log "trimmed oldest ${trim_percent}% from log: $file"
}

delete_old_logs() {
  local dir="$1"
  if [[ ! -d "$dir" ]]; then
    return 0
  fi

  local delete_percent files_count delete_count
  delete_percent=$(clamp_percent "$DELETE_OLDEST_PERCENT")
  mapfile -t old_files < <(
    find "$dir" -maxdepth 1 -type f \
      \( -name '*.log.*' -o -name '*.out.*' -o -name '*.err.*' -o -name '*.tmp' -o -name '*.cache' \) \
      -mtime +"$DELETE_AFTER_DAYS" -printf '%T@|%p\n' 2>/dev/null | sort -n
  )

  files_count=${#old_files[@]}
  if (( files_count == 0 )); then
    return 0
  fi

  delete_count=$(( files_count * delete_percent / 100 ))
  if (( delete_count <= 0 )); then
    delete_count=1
  fi

  local i file_path
  for (( i=0; i<delete_count && i<files_count; i++ )); do
    file_path="${old_files[$i]#*|}"
    if [[ -n "$file_path" && -f "$file_path" ]]; then
      printf '%s\n' "$file_path"
      rm -f -- "$file_path" 2>/dev/null || true
    fi
  done
}

cleanup_python_caches() {
  if [[ ! -d "$APP_DIR" ]]; then
    return 0
  fi

  find "$APP_DIR" -type d -name '__pycache__' -prune -print -exec rm -rf {} + 2>/dev/null || true
  find "$APP_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
}

cleanup_tmp_files() {
  find /tmp -xdev -type f -mtime +"$DELETE_AFTER_DAYS" -print -delete 2>/dev/null || true
  find /var/tmp -xdev -type f -mtime +"$DELETE_AFTER_DAYS" -print -delete 2>/dev/null || true
}

main() {
  local before after
  before=$(disk_usage_percent)
  log "disk usage on $TARGET_PATH before cleanup: ${before}%"

  if (( FORCE_RUN == 0 && before < THRESHOLD_PERCENT )); then
    log "below threshold ${THRESHOLD_PERCENT}%, skip cleanup"
    exit 0
  fi

  # zm-auto 实际产生的大日志文件
  trim_large_log "$APP_DIR/server.stdout.log"
  trim_large_log "$APP_DIR/server.stderr.log"
  trim_large_log "$APP_DIR/data/server.log"
  trim_large_log "$APP_DIR/data/register.log"

  delete_old_logs "$APP_DIR"
  delete_old_logs "$APP_DIR/data"
  cleanup_python_caches
  cleanup_tmp_files

  journalctl --vacuum-time=7d >/dev/null 2>&1 || true
  apt-get clean >/dev/null 2>&1 || true

  after=$(disk_usage_percent)
  log "disk usage on $TARGET_PATH after cleanup: ${after}%"
}

main "$@"
