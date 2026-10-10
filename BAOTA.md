# 宝塔部署

你已经取得 auth.json 后，可以用宝塔文件管理上传，用在线终端安装，再由宝塔计划任务每天启动服务。本方式仍采用项目自带的独立 systemd 服务，保留账号隔离和内存上限。

## 1. 上传与解压

使用最新的 `chaoxing-study.zip`（包含 `deploy-baota.sh`）。在宝塔「文件」中进入 `/root`，创建 `chaoxing-upload` 文件夹，进入后上传：

- `chaoxing-study.zip`
- 你取得的 `auth.json`

文件放在 `/root/chaoxing-upload` 这个非网站目录。解压压缩包到当前目录。正确结构：

```text
/root/chaoxing-upload/
  auth.json
  chaoxing-study.zip
  chaoxing-study/
    worker.py
    install.sh
    deploy-baota.sh
    accounts.json
    systemd/
```

如果解压工具多生成了一层目录，以能看到 `deploy-baota.sh` 的文件夹作为下面命令的工作目录。

## 2. 一次安装并导入会话

打开宝塔「终端」，连接本机，使用 root 身份执行：

```bash
cd /root/chaoxing-upload/chaoxing-study
bash deploy-baota.sh /root/chaoxing-upload/auth.json
```

辅助脚本会安装 Ubuntu 24.04 所需环境，导入账号 `me` 的登录会话，关闭此账号原有 systemd 定时器以便改由宝塔调度，最后运行一次不播放视频的 `probe` 检查。代码安装到 `/opt/chaoxing-study`，登录文件安装到 `/opt/chaoxing-study/data/me/auth.json`。

看到 `probe_ok` 和「检查通过」才代表会话与页面检查成功。它还不代表服务器播放计分成功。安装过程任何一步报错，都先处理该步骤，避免继续创建自动任务。

若宝塔终端使用的是具有 sudo 权限的普通管理员，第二行改用 `sudo bash deploy-baota.sh ...`。Ubuntu 24.04 的系统 Python 和项目虚拟环境由脚本安装和管理。

## 3. 手动验证一次播放

在宝塔终端执行：

```bash
systemctl start --no-block chaoxing-study@me.service
journalctl -u chaoxing-study@me.service -n 60 --no-pager
```

启动是异步的，过一两分钟再执行一次日志命令查看结果。`progress` 中 `points` 必须实际增长；最终 `finished` 状态是 `target_reached` 或 `already_reached` 才算达到当日目标。

需要连续看日志可以执行：

```bash
journalctl -fu chaoxing-study@me.service
```

Ctrl+C 退出日志查看，服务继续运行。要停止学习服务：

```bash
systemctl stop chaoxing-study@me.service
```

## 4. 宝塔每日计划任务

进入「计划任务 → 添加任务」，选择「Shell 脚本」。名称填写「超星每日15积分-me」，执行周期每天，执行用户使用 root（若该版本提供此选项）。

先在终端确认服务器时区：

```bash
date '+%F %T %z %Z'
```

如果是 `+0800`，每天05:00对应北京时间05:00。如果是 `+0000`（UTC），每天21:00对应次日北京时间05:00。按服务器时区设置计划任务，不必为了这个项目更改整台服务器的时区。

脚本内容：

```bash
#!/bin/bash
/usr/bin/systemctl reset-failed chaoxing-study@me.service
/usr/bin/systemctl start chaoxing-study@me.service
task_rc=$?
/usr/bin/journalctl -u chaoxing-study@me.service --since '-50 minutes' --no-pager -n 100
exit "$task_rc"
```

这个脚本等待学习任务结束，再把服务日志写入宝塔任务日志。任务可能运行几十分钟，执行后不要反复点击执行。非0状态保留为失败，不会把仅仅成功启动当作学习成功。

浏览器或运行错误时，systemd 最多自动重启两次，每次间隔2分钟（首次运行加两次重试）。无积分、需要登录/验证、课程身份不符、重复任务或手动停止会直接结束，不自动反复尝试。每日脚本开头的 `reset-failed` 会清除上次的失败计数；每个账号都要对自己的 `@账号.service` 执行这行。当天3次均失败后若要手动再试，先执行 `systemctl reset-failed chaoxing-study@friend1.service`，再启动该账号服务。

提交保存后，以后可在宝塔中暂停、修改运行时间、立即执行和查看日志。此前的 systemd timer 已由辅助脚本关闭，不要再同时启用 `chaoxing-study@me.timer`。

## 5. 后续账号

每位账号持有人先按 README 在自己的电脑登录，导出自己的 `auth.json`，同时取得自己的 `/plaza/` 课程首页完整链接。不要复制 `me` 的 personId/userId，也不要把同一份 auth.json 当作多个账号。

最新版提供 `import_account.py`，可以自动新增账号配置和登录文件，不用手动编辑 JSON。它只检查文件格式和重复配置，不能仅凭导入认定登录会话属于填写的账号；仍需本人确认并进行 probe 和实际播放验收。

已有安装只需把 `import_account.py` 上传到 `/root/chaoxing-upload/chaoxing-study/`，然后以 root 执行一次：

```bash
install -o root -g chaoxing-study -m 0640 /root/chaoxing-upload/chaoxing-study/import_account.py /opt/chaoxing-study/import_account.py
```

将第二个账号的会话文件以 `friend1-auth.json` 名称上传到 `/root/chaoxing-upload/`，然后执行（务必替换引号里的首页地址）：

```bash
/opt/chaoxing-study/.venv/bin/python /opt/chaoxing-study/import_account.py friend1 --auth /root/chaoxing-upload/friend1-auth.json --home-url '同学自己的完整课程首页地址'
```

出现 `IMPORTED` 代表配置和登录文件已经添加，但没有启动浏览器或定时任务。后续账号分别使用 `friend2`、`friend3` 等标识和各自的会话文件、首页链接。重复标识、重复 userId、已有数据目录或不符合格式的文件会停止导入，避免覆盖已有账号。每个导入串行提交配置，现有运行中的账号继续使用其启动时读取的配置。

登录文件安装到 `data/friend1/auth.json`，目录权限0700、文件0600，所有者为 `chaoxing-study`；账号配置原子更新为 root 管理的文件。账号运行隔离使用独立浏览器、数据库和 systemd cgroup；所有账号进程仍使用同一个系统用户，不能将此理解为账号间操作系统权限隔离。

当前约1GB主机上单账号播放采样约365–436MiB、probe估计峰值449MiB。先等正在运行的账号结束，再单独检查新账号，避免 probe 与现有浏览器同时占用内存：

```bash
runuser -u chaoxing-study -- env PLAYWRIGHT_BROWSERS_PATH=/opt/chaoxing-study/browsers /opt/chaoxing-study/.venv/bin/python /opt/chaoxing-study/worker.py probe friend1
```

检查通过后单独启动一次 `chaoxing-study@friend1.service` 验证积分。升级4GB后可以从两个账号开始并发验收：

```bash
systemctl start --no-block chaoxing-study@me.service chaoxing-study@friend1.service
```

并发验收需要两个账号均未达到当日目标，且各自视频进度、积分持续增长；`already_reached` 立即退出不代表验证了多路播放。是否能增加更多账号仍由实际内存、CPU及其他服务器任务决定，导入数量不等于可同时运行数量。

宝塔为每个账号创建独立的每日 Shell 任务，将脚本中的 `@me.service` 换成 `@friend1.service`，并确保 `reset-failed` 和 `start` 两行都使用同一个账号。安排在相同时间便是并行运行。并发数量仍以实际CPU、内存和到账积分验收。

## 6. 旧安装的 AppArmor 沙箱启动错误

若日志同时包含 `No usable sandbox` 和 AppArmor 对 `unprivileged_userns` 的拒绝，这是浏览器创建沙箱时被阻止。最新版安装脚本已经处理此步骤；已装好的服务器可以仅上传最新版 `repair-sandbox.sh` 到 `/root/chaoxing-upload/chaoxing-study`，然后以 root 执行：

```bash
bash /root/chaoxing-upload/chaoxing-study/repair-sandbox.sh
```

脚本仅给当前固定版本 Chromium 可执行文件添加 `userns` 许可，配置写入 `/etc/apparmor.d/chaoxing-study-chromium`，保持浏览器启用沙箱。为了防止运行账号替换被授权的浏览器，项目根目录和浏览器缓存改由 root 管理；data 目录权限保持原样。脚本只重新加载本项目配置，并检查空白页能否打开。若已有本项目配置，会先备份到 `/var/backups/chaoxing-study-sandbox.*`。

出现 `BROWSER_OK` 后执行会话检查：

```bash
runuser -u chaoxing-study -- env PLAYWRIGHT_BROWSERS_PATH=/opt/chaoxing-study/browsers /opt/chaoxing-study/.venv/bin/python /opt/chaoxing-study/worker.py probe me
```

看到 `probe_ok` 后，回到第3步验证播放及实际积分。旧会话文件可以继续用于这次检查，不需要为这个沙箱错误重新登录。

脚本默认路径对应 Playwright 1.63.0 的 headless shell 1243。将来升级浏览器时，必须根据新的启动日志把完整可执行文件路径作为脚本第一个参数，再检查启动；配置只授权一个确切路径，不使用版本通配符。

## 7. 切课时只有 Error、没有具体异常

旧版本最后可能只显示 `event: failed`、`error_type: Error`。此时不能从 `5/NOTINSTALLED` 判断缺少依赖：程序的通用异常退出码就是5。新版 `worker.py` 会显示 `runtime_error`，包含失败阶段、课程编号、异常内容与代码位置；网址及常见敏感参数会遮盖，不保存页面或登录文件内容。

仅上传最新版 `worker.py` 到 `/root/chaoxing-upload/chaoxing-study/` 即可更新。先确保该账号任务已停止，再执行：

```bash
systemctl stop chaoxing-study@me.service
cp -p /opt/chaoxing-study/worker.py /opt/chaoxing-study/worker.py.bak
install -o root -g chaoxing-study -m 0640 /root/chaoxing-upload/chaoxing-study/worker.py /opt/chaoxing-study/worker.py
systemctl start --no-block chaoxing-study@me.service
journalctl -fu chaoxing-study@me.service
```

这个更新不重新安装依赖，也不改变账号配置或历史数据。原先当天已尝试的课程仍会被轮换规则跳过。如再次失败，保留新日志中的 `runtime_error` 和紧随其后的结果记录，按其中具体异常继续定位。

参考：
- [Chromium 官方说明：按路径授权 AppArmor userns](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md)
- [宝塔在线终端](https://docs.bt.cn/user-guide/xterm/)
- [宝塔计划任务](https://docs.bt.cn/user-guide/crontab/task/)
