import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("study_worker", ROOT / "worker.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
HOME = "https://tsjy.chaoxing.com/plaza/?courseId=1&personId=2&classId=3&userId=4"
CFG = dict(home_url=HOME, target_points=15, headless=True,
           score_interval_seconds=60, no_gain_seconds=240, max_run_minutes=45,
           max_catalog_pages=100, max_no_gain_courses=3)
DAY = "2026-10-09"


def tempdir():
    return tempfile.TemporaryDirectory(dir=os.environ.get("STUDY_TEST_TMP"))


class ValidationTests(unittest.TestCase):
    def test_real_page_score_and_spacing(self):
        for text in ("未达标 今日积分 1 /15 累计积分天数 122 天",
                     "总积分\n1663/4680\n今日积分\n1/15\n累计积分天数"):
            self.assertEqual(w.parse_score(text, 15), (1, 15))

    def test_initial_zero_and_invalid_points_are_not_success(self):
        for text in ("今日积分 0/0", "今日积分 16/15", "今日积分 1/10", "今日积分 未知"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                w.parse_score(text, 15)

    def test_routes_keep_this_accounts_enrollment(self):
        expected = HOME.replace("/plaza/?", "/plaza/score-record?")
        self.assertEqual(w.route_url(HOME, "/plaza/score-record"), expected)

    def test_reject_wrong_host_missing_identity_or_extra_secret(self):
        for url in (HOME.replace("tsjy.chaoxing.com", "evil.example"),
                    HOME.replace("&userId=4", ""), HOME + "&enc=secret",
                    HOME.replace("https:", "http:")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                w.route_url(url, "/plaza/score-record")

    def test_account_path_traversal_rejected(self):
        for account in ("../me", "ME", "a/b", "", "a" * 33):
            with self.subTest(account=account), self.assertRaises(ValueError):
                w.validate_account_id(account)

    def test_duplicate_real_identity_rejected(self):
        with tempdir() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"accounts": {"me": CFG, "friend": CFG}}))
            with self.assertRaisesRegex(ValueError, "同一 userId"):
                w.load_config(path, "me")

    def test_wrong_config_types_rejected(self):
        with tempdir() as tmp:
            path = Path(tmp) / "config.json"
            for changes in ({"headless": "false"}, {"target_points": True},
                            {"target_points": 16}, {"no_gain_seconds": 120,
                                                    "score_interval_seconds": 90}):
                cfg = dict(CFG, **changes)
                path.write_text(json.dumps({"accounts": {"me": cfg}}))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    w.load_config(path, "me")

    def test_bad_other_account_does_not_stop_good_account(self):
        with tempdir() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"accounts": {"me": CFG, "friend": {
                "home_url": "invalid"}}}))
            self.assertEqual(w.load_config(path, "me")["home_url"], HOME)
            with self.assertRaises(ValueError):
                w.load_config(path, "friend")

    def test_player_enrollment_check(self):
        good = "https://mooc1-1.chaoxing.com/mycourse/studentstudy?courseId=1&clazzid=3&cpi=2&enc=x"
        w.validate_player_url(good, HOME)
        for wrong in (good.replace("cpi=2", "cpi=999"),
                      good.replace("courseId=1", "courseId=5"),
                      good.replace("mooc1-1.chaoxing.com", "mooc1-1.chaoxing.com.evil.example")):
            with self.subTest(url=wrong), self.assertRaises(w.StopWork):
                w.validate_player_url(wrong, HOME)

    def test_launch_diagnostic_keeps_cause_and_redacts_urls(self):
        exc = Exception("No usable sandbox; error=https://example.test/?enc=private-token")
        detail = w.launch_error_detail(exc)
        self.assertIn("No usable sandbox", detail)
        self.assertNotIn("private-token", detail)
        self.assertIn("[redacted-url]", detail)

    def test_runtime_diagnostic_redacts_standalone_credentials(self):
        detail = w.error_detail(Exception(
            'Frame was detached\nCookie: UID=private-cookie; SID=private-session\n'
            'Authorization: Bearer private-bearer\n'
            'enc=private-enc&openc=private-openc token=private-token\n'
            '"password": "private password"\n'
            'https%3A%2F%2Fexample.test%2F%3Fenc%3Dprivate-encoded'))
        self.assertIn("Frame was detached", detail)
        self.assertNotIn("private", detail)


class IsolationTests(unittest.TestCase):
    def test_history_is_per_account_and_does_not_repeat_today(self):
        with tempdir() as tmp, patch.object(w, "site_day", return_value=DAY):
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            a.mkdir(); b.mkdir()
            s1, s2 = w.Store(a), w.Store(b)
            cards = [{"chapter": "1"}, {"chapter": "2"}]
            try:
                s1.attempt("1", 3)
                self.assertEqual(s1.candidates(cards), [{"chapter": "2"}])
                self.assertEqual(s2.candidates(cards), cards)
                with patch.object(w, "site_day", return_value="2026-10-10"):
                    self.assertEqual(s1.candidates(cards), [{"chapter": "2"}, {"chapter": "1"}])
            finally:
                s1.finish("done", 0, 3, None)
                s2.finish("done", 0, 0, None)

    def test_lock_blocks_duplicate_process_and_releases(self):
        with tempdir() as tmp:
            lock = Path(tmp) / "lock"
            code = (
                "import importlib.util,sys;from pathlib import Path;"
                "s=importlib.util.spec_from_file_location('w',sys.argv[1]);"
                "w=importlib.util.module_from_spec(s);s.loader.exec_module(w);"
                "x=w.AccountLock(Path(sys.argv[2]));\n"
                "try: x.__enter__()\n"
                "except w.StopWork as e: sys.exit(e.code)\n"
                "x.__exit__()"
            )
            with w.AccountLock(lock):
                result = subprocess.run([sys.executable, "-c", code,
                                         str(ROOT / "worker.py"), str(lock)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 6, result.stderr)
                # A different account remains runnable.
                with w.AccountLock(Path(tmp) / "other"):
                    pass
            with w.AccountLock(lock):
                pass


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    async def sleep(self, delay):
        self.now += delay


class Video:
    def __init__(self, clock, paused_at=None, ended_at=None, stalled=False):
        self.clock, self.paused_at, self.ended_at = clock, paused_at, ended_at
        self.stalled = stalled

    async def evaluate(self, script):
        now = self.clock.now
        return dict(time=0 if self.stalled else now, duration=1000, ready=4,
                    paused=self.paused_at is not None and now >= self.paused_at,
                    ended=self.ended_at is not None and now >= self.ended_at,
                    error=None)


class WatchTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, values, *, paused_at=None, ended_at=None,
                     stalled=False, initial=0, deadline=1000, on_points=None,
                     day=DAY):
        clock = Clock()
        video = Video(clock, paused_at, ended_at, stalled)
        page = types.SimpleNamespace(bring_to_front=AsyncMock())
        score = AsyncMock(side_effect=values)
        with patch.object(w.time, "monotonic", clock.monotonic), \
             patch.object(w.asyncio, "sleep", clock.sleep), \
             patch.object(w, "site_day", return_value=day), \
             patch.object(w, "check_blocked", AsyncMock()), \
             patch.object(w, "find_video", AsyncMock(return_value=(None, video))), \
             patch.object(w, "play_normal", AsyncMock()), \
             patch.object(w, "read_score", score), \
             patch.object(w, "emit"), patch.object(w, "memory_mib", return_value=None):
            result = await w.watch(None, page, CFG, "me", initial,
                                   deadline, DAY, lambda: None, on_points)
        return result, score, clock

    async def test_stops_on_actual_target_without_waiting_for_video_end(self):
        result, score, clock = await self.invoke([1, 15])
        self.assertEqual(result, (15, "target_reached"))
        self.assertEqual(score.await_count, 2)
        self.assertEqual(clock.now, 120)

    async def test_no_credit_triggers_rotation(self):
        result, _, clock = await self.invoke([0, 0, 0, 0])
        self.assertEqual(result, (0, "no_gain"))
        self.assertEqual(clock.now, 240)

    async def test_end_checks_credit_even_before_interval(self):
        result, score, clock = await self.invoke([2], ended_at=20)
        self.assertEqual(result, (2, "ended"))
        self.assertEqual(score.await_count, 1)
        self.assertEqual(clock.now, 20)

    async def test_unexpected_pause_requires_human_instead_of_synthetic_events(self):
        with self.assertRaises(w.StopWork) as exc:
            await self.invoke([], paused_at=40)
        self.assertEqual(exc.exception.status, "playback_paused")

    async def test_stalled_video_is_not_counted_as_progress(self):
        with self.assertRaises(w.StopWork) as exc:
            await self.invoke([0], stalled=True)
        self.assertEqual(exc.exception.status, "playback_stalled")

    async def test_midnight_and_score_regression_stop(self):
        with self.assertRaises(w.StopWork) as exc:
            await self.invoke([], day="2026-10-10")
        self.assertEqual(exc.exception.status, "day_changed")
        with self.assertRaises(w.StopWork) as exc:
            await self.invoke([1], initial=2)
        self.assertEqual(exc.exception.status, "score_regressed")

    async def test_latest_points_survive_later_failure(self):
        latest = []
        with self.assertRaises(w.StopWork) as exc:
            await self.invoke([3], deadline=70, on_points=latest.append)
        self.assertEqual(exc.exception.status, "time_limit")
        self.assertEqual(latest, [3])


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_course_error_records_cause_phase_and_latest_points(self):
        context = types.SimpleNamespace(set_default_timeout=lambda _: None,
                                        set_default_navigation_timeout=lambda _: None)
        browser = types.SimpleNamespace(new_context=AsyncMock(return_value=context),
                                        close=AsyncMock())
        pw = types.SimpleNamespace(chromium=types.SimpleNamespace(
            launch=AsyncMock(return_value=browser)))

        class Driver:
            async def __aenter__(self):
                return pw
            async def __aexit__(self, *args):
                pass

        module = types.ModuleType("playwright.async_api")
        module.async_playwright = Driver
        cards = [dict(chapter=str(i), title=f"Course {i}") for i in (1, 2)]
        page = types.SimpleNamespace(close=AsyncMock())

        async def watched(*args):
            args[-1](3)
            return 3, "ended"

        with tempdir() as tmp:
            folder = Path(tmp)
            (folder / "auth.json").write_text("{}")
            with patch.dict(sys.modules, {"playwright.async_api": module}), \
                 patch.object(w, "read_score", AsyncMock(return_value=2)), \
                 patch.object(w, "collect_cards", AsyncMock(return_value=cards)), \
                 patch.object(w, "open_course", AsyncMock(side_effect=[page, RuntimeError(
                     'Page.goto: net::ERR_ABORTED at https://example.test/?enc=private')])), \
                 patch.object(w, "watch", AsyncMock(side_effect=watched)), \
                 patch.object(w, "memory_mib", return_value=None), patch.object(w, "emit") as emitted:
                with self.assertRaises(w.StopWork) as caught:
                    await w.run(CFG, "me", folder)
                self.assertEqual(caught.exception.status, "runtime_error")
                details = next(call.kwargs for call in emitted.call_args_list
                               if call.args[0] == "runtime_error")
                self.assertEqual(details["phase"], "open_course")
                self.assertEqual(details["chapter"], "2")
                self.assertIn("net::ERR_ABORTED", details["message"])
                self.assertNotIn("private", details["message"])
                self.assertTrue(details["locations"])
            browser.close.assert_awaited_once()
            page.close.assert_awaited_once()
            db = w.sqlite3.connect(folder / "history.sqlite3")
            try:
                self.assertEqual(db.execute(
                    "SELECT status,start_points,end_points FROM runs").fetchone(),
                    ("runtime_error", 2, 3))
            finally:
                db.close()

    async def test_already_complete_does_not_open_or_play_courses(self):
        context = types.SimpleNamespace(set_default_timeout=lambda _: None,
                                        set_default_navigation_timeout=lambda _: None)
        browser = types.SimpleNamespace(new_context=AsyncMock(return_value=context),
                                        close=AsyncMock())
        pw = types.SimpleNamespace(chromium=types.SimpleNamespace(
            launch=AsyncMock(return_value=browser)))

        class Driver:
            async def __aenter__(self):
                return pw
            async def __aexit__(self, *args):
                pass

        module = types.ModuleType("playwright.async_api")
        module.async_playwright = Driver
        with tempdir() as tmp:
            folder = Path(tmp)
            (folder / "auth.json").write_text("{}")
            collect, play = AsyncMock(), AsyncMock()
            with patch.dict(sys.modules, {"playwright.async_api": module}), \
                 patch.object(w, "read_score", AsyncMock(return_value=15)), \
                 patch.object(w, "collect_cards", collect), \
                 patch.object(w, "watch", play), \
                 patch.object(w, "save_auth", AsyncMock()), \
                 patch.object(w, "memory_mib", return_value=None), patch.object(w, "emit"):
                self.assertEqual(await w.run(CFG, "me", folder), 0)
            collect.assert_not_awaited()
            play.assert_not_awaited()
            browser.close.assert_awaited_once()
            db = w.sqlite3.connect(folder / "history.sqlite3")
            try:
                self.assertEqual(db.execute("SELECT status,end_points FROM runs").fetchone(),
                                 ("already_reached", 15))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
