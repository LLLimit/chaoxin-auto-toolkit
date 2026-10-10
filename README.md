# chaoxin-auto-toolkit
超星学习通-2025秋季大学生安全教育每月一课自动刷课工具

# 超星视频学习任务（Ubuntu 24.04）

使用宝塔面板、已取得 auth.json：请优先按 [宝塔部署说明](https://github.com/LLLimit/chaoxin-auto-toolkit/blob/main/BAOTA.md) 操作。

每天通过平台正常播放按钮播放视频，读取实际到账积分，到15分停止。支持多个账号同时运行，账号间分开浏览器进程、登录会话、数据库和 systemd 服务。当前配置只包含你的 `me` 账号。

这是仍需完整验收的部署版本：当前 Chrome 中已观察到正常播放计分；用户提供的服务器日志显示实际视频进度增长，随后确认今日积分从3增至5。达到15分及多账号并发仍未验证。平台规则、登录验证、视频弹题和服务器资源仍会影响运行。程序不伪造播放记录、计时请求或指纹，也不绕过真人验证。

## 实现与性能

- 无头 Chromium：不打开桌面窗口，不录屏，不定时截图；服务器只安装 headless shell。
- 每账号一个独立进程树：Cookie、任务异常和数据库互相隔离。独立 systemd cgroup 对每个账号限制内存，避免某个浏览器无限消耗资源。服务器故障和主机整体资源不足仍可能影响全部账号。
- 播放时关闭目录页；积分检查临时开一个页面，读取后立即关闭。正常视频资源和平台脚本保留。
- 视频状态每10秒检查一次，积分默认每60秒检查一次。播放器和平台积分均有进展才算验证成功。
- 课程按最近尝试时间轮换，同一天不会重复尝试同一课程。默认连续240秒积分不增长就换课，连续3节完全不增长则停止，避免无限空转。
- 达到15分直接退出，已满15分不再播放。最长45分钟；跨北京时间零点停止。同账号重复启动会被操作系统锁拒绝。
- 浏览器或运行错误由 systemd 自动重启，最多总计3次（首次加2次重试，间隔2分钟）；未完成目标、登录/验证、配置错误、身份不符、重复任务和手动停止等状态不会重试。达到启动次数限制后，人工重试前需执行 `sudo systemctl reset-failed chaoxing-study@账号.service`。
- 登录过期、验证提示、异常暂停、播放卡住、页面结构变化均明确记录状态；不会假装任务成功。

本版本优先满足你要求的账号故障隔离，因此没有采用共享一个 Chromium 进程的多会话模式。共享模式能减少浏览器基础开销，但浏览器崩溃会影响所有账号。

## 第一步：在你自己的电脑登录

下载并解压整个文件夹。`accounts.json` 已填入你提供的课程首页，不含密码或 Cookie。

必须先进入包含 `worker.py`、`requirements.txt` 的项目目录，不能在 `C:\Windows\system32` 中执行。你当前电脑上的项目路径如下；若你另外解压到了其他目录，请修改第一行路径。逐行执行，任何一步报错时先停止，不要继续运行后面的命令：

```powershell
Set-Location "C:\Users\DarkNight\Documents\Codex\2026-10-09\pnpm-db-migrate\outputs\chaoxing-study"
Get-Item worker.py, requirements.txt
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe worker.py check me
.\.venv\Scripts\python.exe worker.py login me
```

程序打开一个新浏览器。你在其中自行登录超星、完成平台验证，并打开这门课程，然后回终端按 Enter。程序确认能读取积分后，将登录会话保存到 `data/me/auth.json`。这个文件可用于登录你的账号，只传到你自己的服务器，不要发在聊天或提交到代码仓库。会话能否跨机器继续有效仍由平台决定；服务器要求重新验证时，程序会停止。

如果新浏览器没有自动跳转登录入口，手动进入超星登录页，登录后再打开 `accounts.json` 中的课程首页。

## 第二步：安装到服务器

将程序目录上传到 Ubuntu 24.04，例如 `~/chaoxing-study`。在该目录运行：

```bash
sudo bash install.sh
```

脚本创建专用系统用户，把代码安装到 `/opt/chaoxing-study`，安装 Python 虚拟环境、固定版本 Playwright、Chromium 系统依赖和 headless shell，注册 systemd 服务。脚本不会启动学习，也不会覆盖已经存在的 `accounts.json` 或账号数据。

将你电脑上的 `data/me/auth.json` 安全上传到服务器临时位置，例如用 `scp`（请把 `your-server` 换成你的服务器）：

```powershell
scp .\data\me\auth.json your-user@your-server:~/me-auth.json
```

在服务器完成导入：

```bash
sudo install -d -o chaoxing-study -g chaoxing-study -m 0700 /opt/chaoxing-study/data/me
sudo install -o chaoxing-study -g chaoxing-study -m 0600 ~/me-auth.json /opt/chaoxing-study/data/me/auth.json
```

程序运行时启用 Chromium sandbox。安装脚本会为固定版本 headless shell 的确切路径安装 AppArmor `userns` 许可，并运行空白页启动检查；浏览器缓存和项目目录由 root 管理，运行账号只写入 data。它不会更改全局内核设置。旧安装遇到 `No usable sandbox` 且内核日志出现 `unprivileged_userns` 拒绝时，按 [宝塔部署说明](BAOTA.md) 的修复步骤处理。

## 第三步：先验收，再设置每天运行

先确认服务器能用该会话读取积分和视频目录；`probe` 不播放视频：

```bash
sudo -u chaoxing-study env PLAYWRIGHT_BROWSERS_PATH=/opt/chaoxing-study/browsers /opt/chaoxing-study/.venv/bin/python /opt/chaoxing-study/worker.py probe me
```

启动单账号正常播放测试：

```bash
sudo systemctl start --no-block chaoxing-study@me.service
sudo journalctl -fu chaoxing-study@me.service
```

日志中 `progress` 的 `points` 必须增长；`finished` 为 `target_reached` 或 `already_reached` 才算当日目标完成。`probe_ok` 只代表能读取页面，不代表视频计分已验证。

查看任务结果和内存记录：

```bash
sudo -u chaoxing-study /opt/chaoxing-study/.venv/bin/python /opt/chaoxing-study/worker.py report me
sudo systemctl show chaoxing-study@me.service -p Result -p MemoryPeak
```

`peak_memory_mib` 在 systemd 下是账号进程树的 cgroup 内存采样峰值；采样可能漏掉短时峰值，`MemoryPeak` 更适合验收。直接命令运行时使用子进程 RSS 估计，会重复计算共享内存，不能当作精确物理内存值。

验收通过后设置每天北京时间21:00运行：

```bash
sudo systemctl enable --now chaoxing-study@me.timer
systemctl list-timers 'chaoxing-study@*'
```

时间可通过 `sudo systemctl edit chaoxing-study@me.timer` 修改，例如：

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 20:30:00 Asia/Hong_Kong
```

修改后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart chaoxing-study@me.timer
```

立即停止该账号：

```bash
sudo systemctl stop chaoxing-study@me.service
```

## 扩展到多个账号并行

服务器可用 `import_account.py` 添加新账号，无需手动合并配置；完整命令见 [宝塔部署说明第5节](BAOTA.md)。导入仅添加数据，不自动开启浏览器或计划任务。当前1GB服务器先单独验收每个账号；扩容后再从两个账号开始并发验收。

每位账号持有人在自己的电脑用新的账号标识登录，例如 `friend1`。先在各自 `accounts.json` 增加配置，`home_url` 必须来自该账号自己的课程首页；不能复制你的 personId/userId 给同学。其他参数可以复制 `me` 的值。

```json
{
  "accounts": {
    "me": {"home_url": "你的完整课程首页地址"},
    "friend1": {"home_url": "同学自己的完整课程首页地址"}
  }
}
```

分别执行 `worker.py login friend1`，把会话文件导入服务器的 `data/friend1/auth.json`，权限和 `me` 相同；同时更新服务器 `accounts.json`。不同账号必须使用不同 `userId`，程序会拒绝重复身份配置。

然后启用对应定时器：

```bash
sudo systemctl enable --now chaoxing-study@friend1.timer
```

定时器在同一时间触发，账号真正并行运行。某个账号登录过期、验证失败或浏览器退出，不会让其他账号的服务退出。同一账号不允许多个副本并行。

**内存需要实测决定并发数。** 默认每账号 `MemoryHigh=600M`、`MemoryMax=750M` 是资源保护上限，不是实测内存需求，也不是保证可以完成播放的额度。1GB先验收一个账号；升级4GB后逐个增加并发，不要直接假设能支持固定数量。每个账号可能因达到内存上限而失败；主机应保留系统和其他服务的内存余量。

需要调整某账号的上限时：

```bash
sudo systemctl edit chaoxing-study@me.service
```

例如输入：

```ini
[Service]
MemoryHigh=650M
MemoryMax=800M
```

再执行 `sudo systemctl daemon-reload`。增加并发前应确认所有账号内存预算加上系统和其他服务的预算不超过物理内存。2核CPU能否维持多路视频实时解码，同样以实际播放进度和到账积分验证。

## 配置与失败状态

| 字段 | 默认值 | 含义 |
|---|---:|---|
| target_points | 15 | 当日实际积分目标 |
| headless | true | 服务器无头运行；false需要完整Chromium及可用桌面显示 |
| score_interval_seconds | 60 | 积分检查间隔 |
| no_gain_seconds | 240 | 连续多久不涨分就换课 |
| max_run_minutes | 45 | 本次最长运行时间 |
| max_no_gain_courses | 3 | 连续无收益课程达到多少节时停止 |
| max_catalog_pages | 100 | 目录扫描最大页数 |

| 状态 | 处理 |
|---|---|
| target_reached / already_reached | 当日积分目标完成 |
| needs_login / needs_verification | 本人重新登录或完成验证 |
| playback_paused | 检查视频弹题、页面前台要求或本人验证 |
| playback_stalled / media_error | 检查网络、CPU资源和媒体错误 |
| no_gain | 当前播放未到账；检查重复观看规则、上报延迟或平台限制 |
| score_unavailable / catalog_changed / course_open_failed | 页面未加载或结构变化，需要适配 |
| enrollment_mismatch | 配置与播放器学习者不一致，先核对账号资料 |
| time_limit / courses_exhausted | 本次未完成目标，不能当作成功 |

退出码：0为成功或probe成功；2为未完成目标；3为本人交互；4为配置/页面问题；5为浏览器/网络/媒体错误；6为重复任务；130为手动中断。不会自动发送邮件、短信或第三方消息；由 systemd 和本地日志记录结果。

课程视频时长上限和重复播放限制没有从页面得到完整规则，轮换只能减少重复尝试，不能保证每一节都计分。首次读到15分即停止，检查间隔内若超出目标，平台本身的每日上限仍负责限制计分。视频之外的阅读和答题分项要求仍需自行完成。

## 本地检查

```bash
python -m unittest discover -s tests -v
python worker.py check me
```

这些测试覆盖积分解析、跨账号配置、锁、历史轮换、到账后停止和异常暂停等程序行为；没有模拟一个假平台来宣称真实超星计分测试通过。

参考：
- [Playwright Python 安装](https://playwright.dev/python/docs/intro)
- [登录会话保存与恢复](https://playwright.dev/python/docs/auth)
- [浏览器会话隔离](https://playwright.dev/docs/browser-contexts)
