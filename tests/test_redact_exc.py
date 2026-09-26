"""log.exception() のトレースバックにもパスワードが残らないこと。"""
from __future__ import annotations

import io
import logging

from rakuraku import redact


def test_exception_traceback_is_redacted():
    redact.clear_secrets()
    redact.register_secret("Sup3r-Secret!")
    logger = logging.getLogger("rakuraku.test.exc")
    logger.propagate = False
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    redact.install(logger)
    try:
        try:
            raise RuntimeError("Locator.fill: Timeout, value=Sup3r-Secret! password=Sup3r-Secret!")
        except RuntimeError:
            logger.exception("stream unexpected error")
    finally:
        logger.removeHandler(handler)
        redact.clear_secrets()
    out = buf.getvalue()
    assert "Sup3r-Secret!" not in out
    assert "Traceback" in out and "***" in out
