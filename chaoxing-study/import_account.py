"""Add an account on the server without starting a browser or changing others."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from worker import route_url, validate_account_id


def merged_config(document: dict, account: str, home: str) -> dict:
    validate_account_id(account)
    route_url(home, "/plaza/score-record")
    accounts = document["accounts"]
    if not isinstance(accounts, dict):
        raise ValueError("accounts 必须是对象")
    if account in accounts:
        raise ValueError("此账号标识已经存在；本工具仅添加新账号")
    identity = parse_qs(urlsplit(home).query)["userId"][0]
    for entry in accounts.values():
        other_home = entry.get("home_url", "")
        other = parse_qs(urlsplit(other_home).query).get("userId", [])
        if other == [identity]:
            raise ValueError("这个 userId 已经导入，不能重复作为另一个账号")
    result = dict(document)
    result["accounts"] = dict(accounts)
    result["accounts"][account] = dict(home_url=home, target_points=15, headless=True)
    return result


def read_session(path: Path) -> bytes:
    state = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) \
            or not isinstance(state.get("origins"), list):
        raise ValueError("登录文件必须是 Playwright 导出的 auth.json")
    if not any(isinstance(cookie, dict) and
               str(cookie.get("domain", "")).lstrip(".").split(".")[-2:] ==
               ["chaoxing", "com"] for cookie in state["cookies"]):
        raise ValueError("登录文件中没有超星 Cookie，请先由账号持有人登录导出")
    # Format checking alone cannot establish which account this session belongs to.
    return json.dumps(state, ensure_ascii=False).encode("utf-8")


def add_account(config: Path, data: Path, account: str, session: Path, home: str,
                owner: tuple[int, int] | None = None):
    document = json.loads(config.read_text(encoding="utf-8-sig"))
    updated = merged_config(document, account, home)
    auth_bytes = read_session(session)
    folder = data / account
    if folder.exists() or folder.is_symlink():
        raise ValueError("该账号数据目录已经存在；停止以免覆盖已有会话")
    # Stage config in the same filesystem before creating any account data.
    fd, name = tempfile.mkstemp(prefix=".accounts-import-", dir=config.parent)
    staged = Path(name)
    created = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(updated, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        folder.mkdir(mode=0o700)
        created = True
        auth = folder / "auth.json"
        auth_fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(auth_fd, "wb") as out:
            out.write(auth_bytes)
            out.flush()
            os.fsync(out.fileno())
        if owner is not None:
            os.chown(auth, *owner)
            os.chown(folder, *owner)
            os.chown(staged, 0, owner[1])
        staged.chmod(0o640)
        os.replace(staged, config)
    except Exception:
        if created:
            (folder / "auth.json").unlink(missing_ok=True)
            folder.rmdir()
        raise
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account", help="新账号标识，例如 friend1")
    parser.add_argument("--auth", type=Path, required=True)
    parser.add_argument("--home-url", required=True, help="该账号自己的 /plaza/ 首页完整地址")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        parser.exit(1, "请在服务器以 root 身份运行，或使用 sudo。\n")
    import fcntl
    import pwd

    root = Path("/opt/chaoxing-study")
    user = pwd.getpwnam("chaoxing-study")
    try:
        # Serialize imports so simultaneous additions do not lose each other.
        lock_fd = os.open(root / ".accounts-import.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            add_account(root / "accounts.json", root / "data", args.account,
                        args.auth, args.home_url, (user.pw_uid, user.pw_gid))
    except (ValueError, KeyError, TypeError, OSError):
        # Do not dump exceptions from credential parsing into terminal output.
        parser.exit(1, "导入失败：检查账号标识是否重复、本人课程首页、auth.json格式和目录权限。\n")
    print(f"IMPORTED：{args.account} 已导入；没有启动浏览器或计划任务。")
    print("请在内存充足时运行 worker.py probe 检查该会话，然后单独验证计分。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
