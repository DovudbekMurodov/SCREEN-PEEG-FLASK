"""ログにパスワードを絶対に出さないための簡易マスキング。"""
from __future__ import annotations

import logging
import re
import threading

_MASK = "***"
_KEY_VALUE = re.compile(
    r"(?i)(\"?(?:password|passwd|pwd|secret|token)\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_lock = threading.Lock()
_secrets = set()  # type: set[str]


def register_secret(value):
    # type: (str | None) -> None
    if value and len(value) >= 4:
        with _lock:
            _secrets.add(value)


def clear_secrets():
    # type: () -> None
    with _lock:
        _secrets.clear()


def redact(text):
    # type: (str) -> str
    if not text:
        return text
    text = _KEY_VALUE.sub(lambda m: m.group(1) + _MASK, text)
    with _lock:
        values = sorted(_secrets, key=len, reverse=True)
    for value in values:
        if value in text:
            text = text.replace(value, _MASK)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record):  # type: (logging.LogRecord) -> bool
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        # log.exception() のトレースバック (例外メッセージに Playwright の呼び出しログ等が
        # 含まれる) も必ずマスクする: 先に整形して redact し、Formatter にはその文字列を使わせる。
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
                record.exc_info = None
            except Exception:  # noqa: BLE001
                pass
        return True


def install(logger=None):
    # type: (logging.Logger | None) -> None
    (logger or logging.getLogger()).addFilter(RedactingFilter())
