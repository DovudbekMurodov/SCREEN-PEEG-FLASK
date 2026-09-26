"""Playwright ブラウザ起動。ローカル (同梱Chromium) と PythonAnywhere (システムChromium) 両対応。"""
from __future__ import annotations

import contextlib
import os

DEFAULT_TIMEOUT_MS = 15_000
NAV_TIMEOUT_MS = 60_000

# PythonAnywhere 有料プランのシステム Chromium の既定位置。
_PA_CHROMIUM = "/usr/bin/chromium"


def chromium_executable_path():
    # type: () -> str | None
    """明示指定 (RR_CHROMIUM_PATH) → PythonAnywhere の /usr/bin/chromium → None (同梱を使う)。"""
    explicit = os.environ.get("RR_CHROMIUM_PATH")
    if explicit and os.path.isfile(explicit):
        return explicit
    if os.path.isfile(_PA_CHROMIUM):
        return _PA_CHROMIUM
    return None


def playwright_available():
    # type: () -> bool
    """Playwright とブラウザ実行ファイルが使えるかを、起動を試さずに確認する。"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    if chromium_executable_path():
        return True
    # 同梱ブラウザがインストール済みか (playwright install chromium 済みか)。
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser(
        "~/Library/Caches/ms-playwright"
        if os.uname().sysname == "Darwin"
        else "~/.cache/ms-playwright"
    )
    try:
        return os.path.isdir(cache) and any(
            name.startswith("chromium") for name in os.listdir(cache)
        )
    except OSError:
        return False


@contextlib.contextmanager
def browser_session(headless=True, slow_mo_ms=0, proxy_url=None):
    # type: (bool, int, str | None) -> object
    """Chromium を起動し context を1つ返す。終了時に必ず閉じる。"""
    from playwright.sync_api import sync_playwright

    args = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
    launch = {"headless": headless, "args": args}
    exe = chromium_executable_path()
    if exe:
        launch["executable_path"] = exe
    if slow_mo_ms:
        launch["slow_mo"] = slow_mo_ms
    if proxy_url:
        launch["proxy"] = {"server": proxy_url}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        context = browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            accept_downloads=True,
            viewport={"width": 1600, "height": 1000},
        )
        context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        try:
            yield context
        finally:
            with contextlib.suppress(Exception):
                context.close()
            with contextlib.suppress(Exception):
                browser.close()
