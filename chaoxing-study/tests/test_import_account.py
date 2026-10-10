import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    spec = importlib.util.spec_from_file_location("study_import_account", ROOT / "import_account.py")
    admin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admin)
finally:
    sys.path.pop(0)

HOME = "https://tsjy.chaoxing.com/plaza/?courseId=1&personId=2&classId=3&userId=4"
FRIEND_HOME = HOME.replace("personId=2", "personId=22").replace("userId=4", "userId=44")
SESSION = {"cookies": [{"domain": ".chaoxing.com", "name": "session", "value": "private"}],
           "origins": []}


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("STUDY_TEST_TMP"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "accounts.json"
        self.original = {"accounts": {"me": {"home_url": HOME, "no_gain_seconds": 360}},
                         "custom": {"preserve": True}}
        self.cfg.write_text(json.dumps(self.original), encoding="utf-8")
        self.before = self.cfg.read_bytes()
        self.data = self.root / "data"
        (self.data / "me").mkdir(parents=True)
        (self.data / "me" / "auth.json").write_text("existing session", encoding="utf-8")
        self.session = self.root / "friend-auth.json"
        self.session.write_text(json.dumps(SESSION), encoding="utf-8")

    def add(self, home=FRIEND_HOME):
        admin.add_account(self.cfg, self.data, "friend1", self.session, home)

    def assert_unchanged(self):
        self.assertEqual(self.cfg.read_bytes(), self.before)
        self.assertEqual((self.data / "me" / "auth.json").read_text(), "existing session")

    def test_new_account_preserves_existing_config_and_session(self):
        self.add()
        result = json.loads(self.cfg.read_text())
        self.assertEqual(result["accounts"]["me"], self.original["accounts"]["me"])
        self.assertEqual(result["custom"], self.original["custom"])
        self.assertEqual(result["accounts"]["friend1"]["home_url"], FRIEND_HOME)
        self.assertEqual(result["accounts"]["friend1"]["target_points"], 15)
        self.assertEqual(json.loads((self.data / "friend1" / "auth.json").read_text()), SESSION)
        self.assertEqual((self.data / "me" / "auth.json").read_text(), "existing session")

    def test_duplicate_identity_is_rejected_before_any_write(self):
        with self.assertRaises(ValueError):
            self.add(HOME)
        self.assert_unchanged()
        self.assertFalse((self.data / "friend1").exists())

    def test_invalid_session_does_not_change_config(self):
        self.session.write_text('{"cookies": [], "origins": []}')
        with self.assertRaises(ValueError):
            self.add()
        self.assert_unchanged()
        self.assertFalse((self.data / "friend1").exists())

    def test_existing_data_is_never_overwritten(self):
        folder = self.data / "friend1"
        folder.mkdir()
        (folder / "auth.json").write_text("keep this session")
        with self.assertRaises(ValueError):
            self.add()
        self.assert_unchanged()
        self.assertEqual((folder / "auth.json").read_text(), "keep this session")

    def test_config_commit_failure_rolls_back_new_account_only(self):
        with patch.object(admin.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.add()
        self.assert_unchanged()
        self.assertFalse((self.data / "friend1").exists())
        self.assertEqual(list(self.root.glob(".accounts-import-*")), [])


if __name__ == "__main__":
    unittest.main()
