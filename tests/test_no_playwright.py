"""playwright が未インストールでも (PythonAnywhere 無料プラン等) アプリと楽楽ページは起動し、
実行時に 503「有料プラン」を返すこと (404 にならないこと)。別プロセスで playwright を無効化して確認。"""
from __future__ import annotations

import os
import subprocess
import sys

SCRIPT = r"""
import sys
sys.modules["playwright"] = None            # import playwright → ImportError
sys.modules["playwright.sync_api"] = None
import app as appmod
appmod.app.config.update(TESTING=True)
c = appmod.app.test_client()
assert c.get("/").status_code == 200, "index broken"
assert c.get("/kintai").status_code == 200, "kintai page must render (was 404)"
assert c.get("/seisan").status_code == 200, "seisan page must render"
r = c.post("/kintai/run", data={"company_code": "PEEG", "login_id": "a", "password": "b", "months": "2026-09"})
assert r.status_code == 503 and "有料プラン" in r.get_data(as_text=True), (r.status_code, r.get_data(as_text=True)[:200])
r = c.post("/seisan/stream", data={"login_id": "a", "password": "b"})
assert "有料プラン" in r.get_data(as_text=True)
print("OK")
"""


def test_app_serves_rakuraku_pages_without_playwright():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=root, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr
