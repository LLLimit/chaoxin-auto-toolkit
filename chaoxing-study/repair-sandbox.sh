#!/usr/bin/env bash
set -euo pipefail

# Ubuntu 24.04: permit user namespaces for this root-managed browser only.
if [[ "$EUID" -ne 0 ]]; then
  echo '请以 root 身份执行 bash repair-sandbox.sh' >&2
  exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo '此脚本仅适用于 Ubuntu 24.04。' >&2
  exit 1
fi
command -v apparmor_parser >/dev/null || { echo '缺少 apparmor_parser，请先安装 apparmor。' >&2; exit 1; }
id chaoxing-study >/dev/null
task_root=/opt/chaoxing-study
task_cache="$task_root/browsers"
task_executable="${1:-$task_cache/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell}"
task_profile=/etc/apparmor.d/chaoxing-study-chromium

# No globs in the profile; reject symlinked roots and unexpected executables.
if [[ ! "$task_executable" =~ ^/opt/chaoxing-study/browsers/chromium_headless_shell-[0-9]+/chrome-headless-shell-linux64/chrome-headless-shell$ ]]; then
  echo '浏览器路径不符合此项目的安装位置。' >&2
  exit 1
fi
for task_path in "$task_root" "$task_cache" "$task_executable"; do
  if [[ ! -e "$task_path" || "$(realpath -e -- "$task_path")" != "$task_path" ]]; then
    echo "路径不存在或包含符号链接：$task_path" >&2
    exit 1
  fi
done
[[ -x "$task_executable" && -x "$task_root/.venv/bin/python" ]]
if [[ -L "$task_profile" ]]; then
  echo '已有配置文件是符号链接，停止以免修改其他配置。' >&2
  exit 1
fi
if [[ -e "$task_profile" ]] && ! grep -qx '# Managed by chaoxing-study repair-sandbox.sh' "$task_profile"; then
  echo "已有非本项目管理的配置：$task_profile；停止覆盖。" >&2
  exit 1
fi

task_temp="$(mktemp /etc/apparmor.d/.chaoxing-study-chromium.XXXXXX)"
trap 'rm -f -- "$task_temp"' EXIT
cat > "$task_temp" <<EOF
# Managed by chaoxing-study repair-sandbox.sh
abi <abi/4.0>,
include <tunables/global>
profile chaoxing-study-chromium $task_executable flags=(unconfined) {
  userns,
}
EOF
# Compile before changing anything; load only the project's profile below.
apparmor_parser -Q -K "$task_temp"

# The allowlisted binary must not be replaceable by the runtime account.
# Canonical cache path was checked above; find does not follow symbolic links.
chown root:chaoxing-study "$task_root" "$task_cache"
chmod 0750 "$task_root" "$task_cache"
chown -hR root:chaoxing-study "$task_cache"
find -P "$task_cache" \( -type f -o -type d \) -exec chmod go-w {} +
task_parent="$task_executable"
while [[ "$task_parent" != / ]]; do
  if runuser -u chaoxing-study -- test -w "$task_parent"; then
    echo "运行账号仍能修改授权路径：$task_parent；停止加载。" >&2
    exit 1
  fi
  task_parent="$(dirname -- "$task_parent")"
done

if [[ -e "$task_profile" ]]; then
  task_backup="$(mktemp -d /var/backups/chaoxing-study-sandbox.XXXXXX)"
  cp -p -- "$task_profile" "$task_backup/profile"
  echo "原配置备份：$task_backup/profile"
fi
install -o root -g root -m 0644 "$task_temp" "$task_profile"
apparmor_parser -r -K "$task_profile"
echo '已加载此浏览器路径的 userns 许可，正在验证启动……'

runuser -u chaoxing-study -- env PLAYWRIGHT_BROWSERS_PATH="$task_cache" \
  TASK_BROWSER_EXECUTABLE="$task_executable" "$task_root/.venv/bin/python" - <<'PY'
import os
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ["TASK_BROWSER_EXECUTABLE"],
        headless=True,
        chromium_sandbox=True,
    )
    page = browser.new_page()
    page.goto("about:blank")
    print("BROWSER_OK：开启沙箱的浏览器启动成功", flush=True)
    browser.close()
PY
echo '浏览器启动验证通过；下一步运行 worker.py probe me 检查会话与页面。'
