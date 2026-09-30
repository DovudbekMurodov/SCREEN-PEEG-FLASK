"""自動操作中のブラウザ画面を、利用者の画面にリアルタイムで表示する (閲覧専用)。

Chromium の CDP screencast で、画面が描き変わるたびに JPEG の1コマを受け取り、
emit(base64文字列) に渡す。送る間隔の調整 (間引き) は受け取る側 (SSE) で行う。
コマはメモリ上で最新の1枚だけを持ち、サーバには保存しない。
表示するだけで、利用者の操作 (クリック・入力) をブラウザへ送る経路は無い。
"""
from __future__ import annotations

import logging

log = logging.getLogger("rakuraku")

# 1 vCPU のサーバでも自動操作を遅くしないよう、小さめ・粗めのコマにする。
SCREENCAST = {"format": "jpeg", "quality": 50, "maxWidth": 1100, "maxHeight": 700, "everyNthFrame": 2}


class LiveView:
    """context の全ページ (後から開くページ・別ウィンドウ含む) の画面を emit へ流す。"""

    def __init__(self, context, emit):
        self.context = context
        self.emit = emit
        self.closed = False
        self._sessions = []
        try:
            context.on("page", self._attach)
            for page in list(context.pages):
                self._attach(page)
        except Exception:  # noqa: BLE001  (テスト用の擬似 context など)
            self.closed = True

    def _attach(self, page):
        if self.closed:
            return
        try:
            cdp = page.context.new_cdp_session(page)
        except Exception:  # noqa: BLE001
            return

        def on_frame(params):
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:  # noqa: BLE001
                pass
            if not self.closed and params.get("data"):
                self.emit(params["data"])

        cdp.on("Page.screencastFrame", on_frame)
        try:
            cdp.send("Page.startScreencast", SCREENCAST)
            self._sessions.append(cdp)
        except Exception:  # noqa: BLE001
            log.debug("screencast not available for this page")

    def close(self):
        self.closed = True
        try:
            self.context.remove_listener("page", self._attach)
        except Exception:  # noqa: BLE001
            pass
        for cdp in self._sessions:
            try:
                cdp.send("Page.stopScreencast")
            except Exception:  # noqa: BLE001
                pass
        self._sessions = []
