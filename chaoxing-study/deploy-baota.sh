#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo '请在宝塔终端以 root 运行；其他管理员用户使用 sudo bash deploy-baota.sh。' >&2
  exit 1
fi

task_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_auth="${1:-$task_source/../auth.json}"
if [[ ! -f "$task_auth" ]]; then
  echo '未找到 auth.json。请将它与项目压缩包上传到同一目录，或传入完整文件路径。' >&2
  exit 1
fi
python3 -m json.tool "$task_auth" >/dev/null
chmod 0600 "$task_auth"

bash "$task_source/install.sh"
install -d -o chaoxing-study -g chaoxing-study -m 0700 /opt/chaoxing-study/data/me
install -o chaoxing-study -g chaoxing-study -m 0600 "$task_auth" /opt/chaoxing-study/data/me/auth.json

# BaoTa will own the daily schedule; avoid a second systemd timer schedule.
systemctl disable --now chaoxing-study@me.timer

echo '安装和会话导入完成，正在检查服务器能否读取积分与视频目录……'
runuser -u chaoxing-study -- env PLAYWRIGHT_BROWSERS_PATH=/opt/chaoxing-study/browsers \
  /opt/chaoxing-study/.venv/bin/python /opt/chaoxing-study/worker.py probe me
echo '检查通过。下一步手动启动一次播放测试，确认计分后在宝塔添加每日 Shell 计划任务。'
