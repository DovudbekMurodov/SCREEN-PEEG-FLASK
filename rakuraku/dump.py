"""失敗時に実サイトのDOMを保存して、セレクタ修正に使う (開発用)。

RR_DEBUG_DUMP=1 のときだけ動作。パスワードは redact + password欄マスクで残さない。
"""
from __future__ import annotations

import datetime as _dt
import os

from rakuraku.redact import redact


def enabled():
    # type: () -> bool
    return os.environ.get("RR_DEBUG_DUMP") == "1"


def dump(page, service, tag):
    # type: (object, str, str) -> None
    if not enabled() or page is None:
        return
    out_dir = os.environ.get("RR_DEBUG_DIR") or os.path.join(os.getcwd(), "rakuraku_debug")
    os.makedirs(out_dir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(out_dir, "%s_%s_%s" % (service, tag, stamp))
    try:
        html = page.content()
        with open(base + ".html", "w", encoding="utf-8") as fh:
            fh.write(redact(html))
    except Exception:  # noqa: BLE001
        pass
    try:
        page.screenshot(path=base + ".png", full_page=True, mask=[page.locator("input[type=password]")])
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(base + ".url.txt", "w", encoding="utf-8") as fh:
            fh.write(page.url)
    except Exception:  # noqa: BLE001
        pass
