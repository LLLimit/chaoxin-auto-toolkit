#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo '请运行 sudo bash install.sh' >&2
  exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo '这个安装脚本针对 Ubuntu 24.04；其他系统请按 README 手动安装。' >&2
  exit 1
fi

task_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_dest=/opt/chaoxing-study
apt-get update
apt-get install -y python3-venv
if ! id chaoxing-study >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$task_dest" --shell /usr/sbin/nologin chaoxing-study
fi
install -d -o root -g chaoxing-study -m 0750 "$task_dest"
for task_file in worker.py requirements.txt README.md diagnose.py repair-sandbox.sh import_account.py; do
  if [[ "$task_source/$task_file" != "$task_dest/$task_file" ]]; then
    install -o root -g chaoxing-study -m 0640 "$task_source/$task_file" "$task_dest/$task_file"
  fi
done
if [[ ! -e "$task_dest/accounts.json" ]]; then
  install -o root -g chaoxing-study -m 0640 "$task_source/accounts.json" "$task_dest/accounts.json"
fi
install -d -o chaoxing-study -g chaoxing-study -m 0700 "$task_dest/data"
python3 -m venv "$task_dest/.venv"
"$task_dest/.venv/bin/python" -m pip install -r "$task_dest/requirements.txt"
"$task_dest/.venv/bin/python" -m playwright install-deps chromium
install -d -o root -g chaoxing-study -m 0750 "$task_dest/browsers"
env PLAYWRIGHT_BROWSERS_PATH="$task_dest/browsers" \
  "$task_dest/.venv/bin/python" -m playwright install --only-shell chromium
bash "$task_dest/repair-sandbox.sh"
install -m 0644 "$task_source/systemd/chaoxing-study@.service" /etc/systemd/system/chaoxing-study@.service
install -m 0644 "$task_source/systemd/chaoxing-study@.timer" /etc/systemd/system/chaoxing-study@.timer
systemctl daemon-reload
echo '安装完成，尚未启动。下一步按 README 导入本人登录会话、运行 probe/run，再启用定时任务。'
