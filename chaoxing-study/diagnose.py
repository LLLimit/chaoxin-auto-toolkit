"""Test Chromium on about:blank, using the same sandbox as the worker."""
import asyncio
import importlib.metadata
import os
import platform
import sys
from pathlib import Path


async def main():
    from playwright.async_api import async_playwright
    print("Python:", platform.python_version(), flush=True)
    print("Playwright:", importlib.metadata.version("playwright"), flush=True)
    print("UID:", os.geteuid() if hasattr(os, "geteuid") else "Windows", flush=True)
    print("Browser cache:", os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "default"), flush=True)
    mem = Path("/proc/meminfo")
    if mem.exists():
        for line in mem.read_text().splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:")):
                print(line, flush=True)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                page = await browser.new_page()
                await page.goto("about:blank")
                print("BROWSER_OK: Chromium startup and blank page succeeded.", flush=True)
            finally:
                await browser.close()
        return 0
    except Exception as exc:
        # This process never loads account data; browser stderr is diagnostic data.
        print("BROWSER_FAILED:", type(exc).__name__, flush=True)
        print(str(exc), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
