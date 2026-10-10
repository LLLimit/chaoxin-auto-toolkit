"""Normal UI playback, isolated workers, and server-confirmed points.

No forged playback events, seeking, speed changes, fingerprint modifications,
CAPTCHA solving, or interference with the site's tracking code.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

BASE = Path(__file__).resolve().parent
SITE_TZ = dt.timezone(dt.timedelta(hours=8))
CARD = "li.list"
LINK = 'a[href^="javascript:goKnowledge("]'
VIDEO_STATE = """v => ({time: v.currentTime, duration:
    Number.isFinite(v.duration) ? v.duration : null, paused: v.paused,
    ended: v.ended, ready: v.readyState,
    error: v.error ? v.error.code : null})"""


class StopWork(Exception):
    def __init__(self, status: str, message: str, code: int = 2):
        super().__init__(message)
        self.status, self.code = status, code


def site_day() -> str:
    return dt.datetime.now(SITE_TZ).date().isoformat()


def validate_account_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", value):
        raise ValueError("账号标识只允许小写字母、数字、_、-，最长32字符")
    return value


def route_url(home: str, path: str) -> str:
    u = urlsplit(home)
    q = parse_qs(u.query)
    if u.scheme != "https" or u.netloc != "tsjy.chaoxing.com":
        raise ValueError("home_url 必须是 https://tsjy.chaoxing.com 的课程首页")
    if u.path not in ("/plaza/", "/plaza") or u.fragment:
        raise ValueError("请使用 /plaza/ 课程首页地址")
    required = ("courseId", "personId", "classId", "userId")
    if set(q) != set(required) or any(
        len(q[k]) != 1 or not q[k][0].isdigit() for k in required
    ):
        raise ValueError("课程首页必须含完整 courseId/personId/classId/userId")
    return urlunsplit((u.scheme, u.netloc, path, u.query, ""))


def parse_score(text: str, target: int) -> tuple[int, int]:
    m = re.search(r"今日积分\s*(\d+)\s*/\s*(\d+)", text)
    if not m:
        raise ValueError("积分页结构不匹配")
    points, cap = map(int, m.groups())
    # The frontend initially renders 0/0 before real data arrives.
    if cap <= 0 or cap < target or not 0 <= points <= cap:
        raise ValueError("积分尚未加载，或每日上限与配置不符")
    return points, cap


def load_config(path: Path, account: str) -> dict:
    validate_account_id(account)
    all_accounts = json.loads(path.read_text(encoding="utf-8"))["accounts"]
    if account not in all_accounts:
        raise ValueError(f"配置中找不到账号 {account}")
    cfg = dict(all_accounts[account])
    route_url(cfg["home_url"], "/plaza/score-record")
    identity = parse_qs(urlsplit(cfg["home_url"]).query)["userId"][0]
    for key, entry in all_accounts.items():
        if key == account:
            continue
        # A malformed *other* account must not prevent this account from running.
        try:
            validate_account_id(key)
            route_url(entry["home_url"], "/plaza/score-record")
            other = parse_qs(urlsplit(entry["home_url"]).query)["userId"][0]
        except (ValueError, KeyError, TypeError):
            continue
        if identity == other:
            raise ValueError("多个配置使用了同一 userId，不能将同一账号当作不同账号并行")
    defaults = dict(target_points=15, headless=True, score_interval_seconds=60,
                    no_gain_seconds=240, max_run_minutes=45,
                    max_catalog_pages=100, max_no_gain_courses=3)
    for key, value in defaults.items():
        cfg.setdefault(key, value)
    limits = dict(target_points=(1, 15), score_interval_seconds=(30, 300),
                  no_gain_seconds=(120, 1200), max_run_minutes=(5, 45),
                  max_catalog_pages=(1, 200), max_no_gain_courses=(1, 20))
    for key, (low, high) in limits.items():
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError(f"{key} 必须是 {low} 到 {high} 的整数")
    if type(cfg["headless"]) is not bool:
        raise ValueError("headless 必须是 true 或 false")
    if cfg["no_gain_seconds"] < 2 * cfg["score_interval_seconds"]:
        raise ValueError("no_gain_seconds 至少是积分检查间隔的两倍")
    return cfg


def validate_player_url(url: str, home: str):
    u, h = urlsplit(url), parse_qs(urlsplit(home).query)
    p = parse_qs(u.query)
    if u.scheme != "https" or not (u.hostname or "").endswith(".chaoxing.com"):
        raise StopWork("unexpected_player", "播放器跳到了非预期站点", 4)
    if not u.path.endswith("/studentstudy"):
        raise StopWork("unexpected_player", "点击课程后没有到达播放器页面", 4)
    checks = (("courseId", "courseId"), ("classId", "clazzid"),
              ("personId", "cpi"))
    mismatches = [f"首页{home_key}/播放器{player_key}"
                  for player_key, home_key in checks
                  if p.get(player_key) != h.get(home_key)]
    if mismatches:
        # Log field names only, never learner IDs or the player's signed URL.
        raise StopWork("enrollment_mismatch",
                       "播放器与账号首页不匹配：" + "、".join(mismatches), 4)


def emit(event: str, account: str, **fields):
    print(json.dumps(dict(time=dt.datetime.now(SITE_TZ).isoformat(),
                          account=account, event=event, **fields),
                     ensure_ascii=False), flush=True)


def error_detail(exc: Exception) -> str:
    """Keep browser error causes without logging signed URLs or credentials."""
    text = re.sub(r"(?:https?|wss?)://[^\s<>\"']+", "[redacted-url]",
                  str(exc), flags=re.I)
    text = re.sub(r"(?:https?|wss?)%3a%2f%2f[^\s<>\"']+", "[redacted-url]",
                  text, flags=re.I)
    text = re.sub(r"(?im)^(\s*(?:cookie|set-cookie|authorization)\s*:\s*).*$",
                  r"\1[redacted]", text)
    text = re.sub(
        r"(?i)\b(enc|openc|token|access_token|refresh_token|password|passwd|"
        r"authorization|cookie)\b([\"']?\s*[:=]\s*)"
        r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&]+)",
        r"\1\2[redacted]", text)
    return text[:10000]


def launch_error_detail(exc: Exception) -> str:
    return error_detail(exc)


def error_locations(exc: Exception) -> list[dict]:
    # Never record locals, source text, page HTML, or storage state.
    return [dict(file=Path(frame.filename).name, line=frame.lineno,
                 function=frame.name)
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]]


class AccountLock:
    """OS lock; releases on process exit, so no stale PID lockfiles."""
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        if os.fstat(self.file.fileno()).st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise StopWork("already_running", "这个账号已有任务在运行", 6)
        return self

    def __exit__(self, *args):
        if os.name == "nt":
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


class Store:
    def __init__(self, folder: Path):
        self.db = sqlite3.connect(folder / "history.sqlite3")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS courses (
                chapter TEXT PRIMARY KEY, last_attempt REAL NOT NULL,
                last_gain INTEGER NOT NULL, day TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                started TEXT PRIMARY KEY, finished TEXT, status TEXT,
                start_points INTEGER, end_points INTEGER, peak_mib REAL);
        """)
        self.started = dt.datetime.now(SITE_TZ).isoformat()
        self.db.execute("INSERT INTO runs(started,status) VALUES(?,?)",
                        (self.started, "starting"))
        self.db.commit()

    def candidates(self, cards: list[dict]) -> list[dict]:
        history = {r[0]: r[1:] for r in self.db.execute(
            "SELECT chapter,last_attempt,last_gain,day FROM courses")}
        # Every course is attempted at most once per site day by this worker.
        eligible = [c for c in cards if c["chapter"] not in history or
                    history[c["chapter"]][2] != site_day()]
        return sorted(eligible, key=lambda c: history.get(c["chapter"], (0,))[0])

    def attempt(self, chapter: str, gain: int = 0):
        self.db.execute("INSERT OR REPLACE INTO courses VALUES(?,?,?,?)",
                        (chapter, time.time(), gain, site_day()))
        self.db.commit()

    def finish(self, status, start, end, peak):
        self.db.execute("UPDATE runs SET finished=?,status=?,start_points=?,"
                        "end_points=?,peak_mib=? WHERE started=?",
                        (dt.datetime.now(SITE_TZ).isoformat(), status,
                         start, end, peak, self.started))
        self.db.commit()
        self.db.close()


def memory_mib() -> float | None:
    """systemd cgroup memory includes Chromium; otherwise sum descendant RSS."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                name = line[3:]
                # Do not report an enclosing machine/container's total as ours.
                if "chaoxing-study@" in name and name.endswith(".service"):
                    current = Path("/sys/fs/cgroup") / name.lstrip("/") / "memory.current"
                    return round(int(current.read_text()) / 1048576, 1)
        parents, rss = {}, {}
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(") ", 1)[1].split()
                pid = int(path.parent.name)
                parents[pid] = int(fields[1])
                rss[pid] = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            except (OSError, ValueError, IndexError):
                continue
        tree = {os.getpid()}
        while True:
            expanded = tree | {p for p, parent in parents.items() if parent in tree}
            if expanded == tree:
                break
            tree = expanded
        return round(sum(rss.get(pid, 0) for pid in tree) / 1048576, 1)
    except (OSError, ValueError):
        return None


async def check_blocked(page):
    if page.is_closed():
        raise StopWork("page_closed", "播放页面被关闭", 5)
    hostname = urlsplit(page.url).hostname or ""
    if hostname.startswith("passport") or "/login" in urlsplit(page.url).path:
        raise StopWork("needs_login", "登录失效，请重新执行 login", 3)
    for frame in page.frames:
        try:
            text = await frame.locator("body").inner_text(timeout=1500)
        except Exception:
            continue
        if any(t in text for t in ("请完成验证", "拖动滑块", "滑动验证",
                                    "请进行人脸", "请完成身份验证")):
            raise StopWork("needs_verification", "平台要求本人验证，任务已暂停", 3)


async def read_score(context, home: str, target: int) -> int:
    page = await context.new_page()
    try:
        await page.goto(route_url(home, "/plaza/score-record"),
                        wait_until="domcontentloaded")
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            await check_blocked(page)
            text = await page.locator("body").inner_text()
            try:
                return parse_score(text, target)[0]
            except ValueError:
                await asyncio.sleep(0.5)
        raise StopWork("score_unavailable", "25秒内未读到有效积分，停止而非猜测积分", 4)
    finally:
        await page.close()


async def page_cards(page, number: int) -> list[dict]:
    return await page.locator(CARD).evaluate_all(r"""(items, number) => items.flatMap(li => {
        const a = li.querySelector('a[href^="javascript:goKnowledge("]');
        const m = a?.getAttribute('href')?.match(/goKnowledge\(\s*\d+\s*,\s*(\d+)/);
        if (!m || !/视频\s*\d+\s*分钟/.test(li.innerText)) return [];
        return [{chapter: m[1], href: a.getAttribute('href'), page: number,
                 title: li.querySelector('.book-name')?.textContent?.trim() || m[1]}];
    })""", number)


async def wait_cards(page):
    await check_blocked(page)
    try:
        await page.locator(f"{CARD} {LINK}").first.wait_for(state="attached", timeout=25000)
    except Exception:
        await check_blocked(page)
        raise StopWork("catalog_unavailable", "课程列表未加载或页面结构已改变", 4)


async def next_catalog(page, fingerprint: str) -> bool:
    links = page.get_by_role("link", name="下一页", exact=True)
    if not await links.count():
        return False
    await links.click()
    try:
        await page.wait_for_function("""old => {
            const a = document.querySelector('li.list a[href^="javascript:goKnowledge("]');
            return a && a.getAttribute('href') !== old;
        }""", arg=fingerprint, timeout=12000)
        return True
    except Exception:
        await check_blocked(page)
        # A final-page button can be present but do nothing.
        now = await page.locator(f"{CARD} {LINK}").first.get_attribute("href")
        if now == fingerprint:
            return False
        raise StopWork("catalog_changed", "目录分页不稳定，请检查页面", 4)


async def collect_cards(context, cfg, deadline=None) -> list[dict]:
    page = await context.new_page()
    cards, seen = {}, set()
    try:
        await page.goto(route_url(cfg["home_url"], "/plaza/knowledge-all"),
                        wait_until="domcontentloaded")
        for number in range(1, cfg["max_catalog_pages"] + 1):
            if deadline is not None and time.monotonic() >= deadline:
                raise StopWork("time_limit", "目录加载已达到本次时间上限")
            await wait_cards(page)
            fingerprint = await page.locator(f"{CARD} {LINK}").first.get_attribute("href")
            if fingerprint in seen:
                break
            seen.add(fingerprint)
            for card in await page_cards(page, number):
                cards.setdefault(card["chapter"], card)
            if not await next_catalog(page, fingerprint):
                break
        if not cards:
            raise StopWork("no_video_courses", "没有找到视频知识点", 4)
        return list(cards.values())
    finally:
        await page.close()


async def open_course(context, cfg, card):
    """Click site-generated links; never synthesize signed studentstudy URLs."""
    catalog = await context.new_page()
    player = None
    try:
        await catalog.goto(route_url(cfg["home_url"], "/plaza/knowledge-all"),
                           wait_until="domcontentloaded")
        await wait_cards(catalog)
        for _ in range(1, card["page"]):
            old = await catalog.locator(f"{CARD} {LINK}").first.get_attribute("href")
            if not await next_catalog(catalog, old):
                raise StopWork("catalog_changed", "目录页码改变，无法定位课程", 4)
        # Match the exact href already read from this account's catalog.
        matching = catalog.locator(f"a[href={json.dumps(card['href'])}]").first
        if not await matching.count():
            raise StopWork("catalog_changed", "课程链接已改变", 4)
        try:
            async with catalog.expect_popup(timeout=10000) as popup:
                await matching.click()
            player = await popup.value
        except Exception:
            # Also accept a normal navigation in the existing tab.
            if "/studentstudy" in urlsplit(catalog.url).path:
                player = catalog
            else:
                await check_blocked(catalog)
                raise StopWork("course_open_failed", "正常点击未打开播放器，请本人检查课程入口", 4)
        await player.wait_for_load_state("domcontentloaded")
        validate_player_url(player.url, cfg["home_url"])
        await check_blocked(player)
        return player
    finally:
        if catalog is not player:
            await catalog.close()


async def find_video(page):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        await check_blocked(page)
        for frame in page.frames:
            try:
                videos = frame.locator("video")
                if await videos.count():
                    return frame, videos.first
            except Exception as exc:
                # The player can replace its iframe while it initializes.
                # Re-scan the live frame list instead of aborting this account.
                detail = str(exc).lower()
                if "frame was detached" in detail or "frame has been detached" in detail:
                    continue
                raise
        await asyncio.sleep(0.5)
    raise StopWork("video_unavailable", "播放器视频元素未加载", 5)


async def play_normal(frame, video):
    state = await video.evaluate(VIDEO_STATE)
    if not state["paused"]:
        return
    for name in ("播放视频", "播放"):
        button = frame.get_by_role("button", name=name, exact=True)
        if await button.count() and await button.first.is_visible():
            await button.first.click()
            return
    raise StopWork("play_button_missing", "没有找到正常播放按钮", 5)


async def watch(context, page, cfg, account, points, deadline, started_day, sample,
                on_points=None):
    emit("player_stage", account, stage="find_video")
    frame, video = await find_video(page)
    emit("player_stage", account, stage="play_button")
    await page.bring_to_front()
    await play_normal(frame, video)
    initial_state = await video.evaluate(VIDEO_STATE)
    last_time = initial_state["time"]
    emit("playback_started", account, chapter_time=round(last_time, 1),
         paused=initial_state["paused"], ready=initial_state["ready"])
    started = progressed = gained = time.monotonic()
    next_score = started + cfg["score_interval_seconds"]
    startup_grace = started + 30
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise StopWork("time_limit", "已到本次最长运行时间，停止")
        if site_day() != started_day:
            raise StopWork("day_changed", "跨过北京时间零点，停止本次任务")
        await check_blocked(page)
        state = await video.evaluate(VIDEO_STATE)
        sample()
        if state["error"]:
            raise StopWork("media_error", f"播放器报告媒体错误 {state['error']}", 5)
        if state["time"] > last_time + 0.1:
            progressed = now
            last_time = state["time"]
        if state["ended"] or now >= next_score:
            actual = await read_score(context, cfg["home_url"], cfg["target_points"])
            await page.bring_to_front()
            if actual < points:
                raise StopWork("score_regressed", "平台积分减少，停止并检查账号/日期", 4)
            if actual > points:
                gained = time.monotonic()
            points = actual
            if on_points is not None:
                on_points(points)
            emit("progress", account, points=points, target=cfg["target_points"],
                 chapter_time=round(state["time"], 1), memory_mib=memory_mib())
            next_score = time.monotonic() + cfg["score_interval_seconds"]
            if points >= cfg["target_points"]:
                return points, "target_reached"
            if state["ended"]:
                return points, "ended"
        if now > startup_grace and state["paused"]:
            raise StopWork("playback_paused", "视频意外暂停，可能需要本人交互，任务停止", 3)
        if now - progressed > 60:
            raise StopWork("playback_stalled", "60秒没有实际播放进度", 5)
        if now - gained >= cfg["no_gain_seconds"]:
            return points, "no_gain"
        await asyncio.sleep(10)


async def save_auth(context, path: Path):
    # Write atomically; credentials are created only after the user logs in.
    state = await context.storage_state(indexed_db=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(temp, path)
    if os.name != "nt":
        path.chmod(0o600)


async def login(cfg, account, folder):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, chromium_sandbox=True)
        context = await browser.new_context()
        try:
            page = await context.new_page()
            await page.goto(cfg["home_url"], wait_until="domcontentloaded")
            print("请在打开的浏览器中自行登录、完成验证，并打开本课程。", flush=True)
            await asyncio.to_thread(input, "完成后回到终端按 Enter 保存登录会话：")
            points = await read_score(context, cfg["home_url"], cfg["target_points"])
            await save_auth(context, folder / "auth.json")
            emit("login_saved", account, points=points)
        finally:
            await browser.close()


async def run(cfg, account, folder, probe=False):
    from playwright.async_api import async_playwright
    auth = folder / "auth.json"
    if not auth.exists():
        raise StopWork("needs_login", "缺少登录会话，先执行 login", 3)
    store = Store(folder)
    status, start, points, peak = "failed", None, None, None
    deadline = time.monotonic() + cfg["max_run_minutes"] * 60
    started_day = site_day()
    phase, chapter = "browser_launch", None

    def sample():
        nonlocal peak
        value = memory_mib()
        if value is not None:
            peak = max(peak or 0, value)

    def update_points(value):
        nonlocal points
        points = value

    try:
        async with async_playwright() as pw:
            # Each systemd account unit launches its own browser process tree.
            sample()
            emit("browser_launch", account, headless=cfg["headless"], sandbox=True)
            try:
                browser = await pw.chromium.launch(headless=cfg["headless"], chromium_sandbox=True)
            except Exception as exc:
                raise StopWork("browser_launch_failed", launch_error_detail(exc), 5) from None
            emit("browser_started", account)
            sample()
            phase = "load_session"
            context = await browser.new_context(storage_state=str(auth),
                                                viewport={"width": 1024, "height": 768})
            context.set_default_timeout(15000)
            context.set_default_navigation_timeout(30000)
            try:
                sample()
                phase = "read_score"
                start = points = await read_score(context, cfg["home_url"], cfg["target_points"])
                emit("start", account, points=points, target=cfg["target_points"])
                if not probe and points >= cfg["target_points"]:
                    status = "already_reached"
                    return 0
                phase = "collect_catalog"
                cards = await collect_cards(context, cfg, deadline)
                emit("catalog", account, videos=len(cards))
                if probe:
                    status = "probe_ok"
                    return 0
                failures = 0
                for card in store.candidates(cards):
                    if time.monotonic() >= deadline:
                        raise StopWork("time_limit", "已到本次最长运行时间，停止")
                    if site_day() != started_day:
                        raise StopWork("day_changed", "跨过北京时间零点，停止本次任务")
                    before = points
                    chapter = card["chapter"]
                    store.attempt(card["chapter"])
                    emit("course", account, chapter=card["chapter"], title=card["title"])
                    phase = "open_course"
                    page = await open_course(context, cfg, card)
                    emit("course_opened", account, chapter=chapter)
                    try:
                        phase = "watch_course"
                        points, reason = await watch(context, page, cfg, account,
                                                     points, deadline, started_day, sample,
                                                     update_points)
                    finally:
                        await page.close()
                    store.attempt(card["chapter"], points - before)
                    emit("course_finished", account, chapter=card["chapter"],
                         gain=points - before, reason=reason)
                    if points >= cfg["target_points"]:
                        status = "target_reached"
                        return 0
                    failures = failures + 1 if points == before else 0
                    if failures >= cfg["max_no_gain_courses"]:
                        raise StopWork("no_gain", "连续多节课程没有到账积分，停止并检查平台规则")
                raise StopWork("courses_exhausted", "今天可轮换的课程已用完，尚未达到目标")
            except StopWork as exc:
                status = exc.status
                raise
            finally:
                sample()
                # Session updates are persisted only for a valid, non-blocked run.
                try:
                    if status in ("target_reached", "already_reached", "probe_ok"):
                        await save_auth(context, auth)
                finally:
                    await browser.close()
    except StopWork as exc:
        status = exc.status
        raise
    except Exception as exc:
        status = "runtime_error"
        emit("runtime_error", account, phase=phase, chapter=chapter,
             error_type=type(exc).__name__, message=error_detail(exc),
             locations=error_locations(exc))
        raise StopWork(status, "运行失败，具体原因见 runtime_error 日志", 5) from None
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    finally:
        store.finish(status, start, points, peak)
        emit("finished", account, status=status, start_points=start,
             end_points=points, peak_memory_mib=peak)


def report(folder):
    path = folder / "history.sqlite3"
    if not path.exists():
        print("还没有运行记录")
        return
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 10").fetchall()
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("login", "probe", "run", "report", "check"))
    parser.add_argument("account")
    parser.add_argument("--config", type=Path, default=BASE / "accounts.json")
    parser.add_argument("--data", type=Path, default=BASE / "data")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config, args.account)
        if args.command == "check":
            emit("config_ok", args.account)
            return 0
        folder = args.data.resolve() / validate_account_id(args.account)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            folder.chmod(0o700)
        if args.command == "report":
            report(folder)
            return 0
        with AccountLock(folder / "worker.lock"):
            if args.command == "login":
                asyncio.run(login(cfg, args.account, folder))
                return 0
            return asyncio.run(run(cfg, args.account, folder, args.command == "probe"))
    except StopWork as exc:
        emit("stopped", args.account, status=exc.status, message=str(exc))
        return exc.code
    except KeyboardInterrupt:
        emit("interrupted", args.account)
        return 130
    except (ValueError, KeyError, OSError) as exc:
        # Do not log signed URLs or credential-bearing browser exceptions.
        fields = {"error_type": type(exc).__name__}
        if isinstance(exc, ValueError):
            fields["message"] = str(exc)
        emit("configuration_or_io_error", args.account, **fields)
        print("请检查配置、目录权限和部署说明。", file=sys.stderr)
        return 4
    except Exception as exc:
        emit("failed", args.account, error_type=type(exc).__name__,
             message=error_detail(exc), locations=error_locations(exc))
        print("任务停止；请检查网络、Playwright安装或平台页面变化。", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
