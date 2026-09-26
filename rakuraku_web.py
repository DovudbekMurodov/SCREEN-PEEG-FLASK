"""楽楽精算 / 楽楽勤怠 ダウンロード機能の Flask Blueprint。

既存の app.py には登録の1行だけを足す。サーバにデータは保存しない:
認証情報はフォームで受け取り、メモリ上で Playwright に渡し、生成物は
一時ファイル→レスポンス配信後に破棄する。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import queue
import threading
import zipfile

from flask import Blueprint, Response, render_template, request, send_file

from i18n import current_lang, translate
from rakuraku import redact
from rakuraku.browser import browser_session, playwright_available
from rakuraku.errors import SERVICE_NAMES, PlaywrightUnavailable, RakuError
from rakuraku.kintai import KintaiClient
from rakuraku.params import (
    ParamError,
    STATUS_ALL,
    default_kintai_months,
    default_seisan_range,
    parse_kintai,
    parse_seisan,
    today_jst,
    year_month,
)
from rakuraku.seisan import SeisanClient

log = logging.getLogger("rakuraku")
redact.install(log)

bp = Blueprint("rakuraku", __name__)

SEISAN_BASE_URL = os.environ.get("RR_SEISAN_BASE_URL", "https://rswaltz.rakurakuseisan.jp/qHKmc0veJHa/")
KINTAI_LOGIN_URL = os.environ.get("RR_KINTAI_LOGIN_URL", "https://tms.kinnosuke.jp/app/login")
KINTAI_ATTENDANCE_URL = os.environ.get(
    "RR_KINTAI_ATTENDANCE_URL", "https://tms.kinnosuke.jp/app/attendancemanagement"
)
KINTAI_COMPANY_DEFAULT = os.environ.get("RR_KINTAI_COMPANY_CODE", "PEEG")
HEADLESS = os.environ.get("RR_HEADLESS", "1") != "0"
SLOWMO_MS = int(os.environ.get("RR_SLOWMO_MS", "0") or 0)
PROXY_URL = os.environ.get("RR_OUTBOUND_PROXY_URL") or None
KINTAI_MAX_MONTHS = int(os.environ.get("RR_KINTAI_MAX_MONTHS", "3") or 3)


def _localize_error(exc, lang=None):
    # type: (RakuError, str | None) -> str
    lang = lang or current_lang()
    template = getattr(exc, "template", None) or getattr(exc, "user_message", "")
    text = translate(template, lang)
    if "{service}" in text:
        name = SERVICE_NAMES.get(exc.service) if exc.service else "楽楽精算・楽楽勤怠"
        text = text.format(service=name)
    return text


# ------------------------------------------------------------ streaming (live steps)
def _sse(event, obj):
    # type: (str, dict) -> str
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(obj, ensure_ascii=False))


def _stream(steps, run, lang):
    # type: (list, object, str) -> Response
    """run(on_step) -> (filename, bytes, mime) をワーカースレッドで実行し、
    ステップ進捗を SSE で流し、最後にファイルを base64 で送る。サーバに保存しない。"""

    def gen():
        q = queue.Queue()
        holder = {}

        def on_step(key, status, detail=None):
            q.put(("step", {"key": key, "status": status, "detail": detail}))

        def work():
            try:
                name, data, mime = run(on_step)
                holder["file"] = (name, data, mime)
                q.put(("done", None))
            except RakuError as exc:
                log.warning("stream failed: %s", getattr(exc, "code", "?"))
                q.put(("error", _localize_error(exc, lang)))
            except Exception:  # noqa: BLE001
                log.exception("stream unexpected error")
                q.put(("error", translate("自動処理でエラーが発生しました。", lang)))
            finally:
                q.put((None, None))

        threading.Thread(target=work, daemon=True).start()
        yield _sse("steps", {"steps": steps})
        while True:
            typ, payload = q.get()
            if typ is None:
                break
            if typ == "done":
                name, data, mime = holder["file"]
                yield _sse("file", {"filename": name, "mime": mime, "b64": base64.b64encode(data).decode()})
            elif typ == "error":
                yield _sse("error", {"message": payload})
            else:
                yield _sse(typ, payload)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse_single_error(message):
    # type: (str) -> Response
    def gen():
        yield _sse("error", {"message": message})

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------- 楽楽精算
@bp.get("/seisan")
def seisan_page():
    dfrom, dto = default_seisan_range()
    return render_template(
        "seisan.html", error=None, statuses_all=STATUS_ALL, default_statuses=["承認依頼中"],
        applied_from=dfrom.isoformat(), applied_to=dto.isoformat(),
    )


@bp.post("/seisan/run")
def seisan_run():
    try:
        params = parse_seisan(request.form)
    except ParamError as exc:
        return _seisan_error(translate(str(exc), current_lang()), 400)

    if not playwright_available():
        return _seisan_error(_localize_error(PlaywrightUnavailable()), 503)

    try:
        with browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
            client = SeisanClient(ctx, SEISAN_BASE_URL, log=lambda m, lvl="info": log.info("%s", m))
            try:
                name, data = client.download(params)
            finally:
                client.logout()
    except RakuError as exc:
        log.warning("seisan failed: %s", exc.code)
        return _seisan_error(_localize_error(exc), 502)
    except Exception:  # noqa: BLE001
        log.exception("seisan unexpected error")
        return _seisan_error(translate("自動処理でエラーが発生しました。", current_lang()), 500)

    return send_file(
        io.BytesIO(data), as_attachment=True, download_name=name, mimetype="text/csv",
    )


def _seisan_error(message, status):
    dfrom, dto = default_seisan_range()
    return render_template(
        "seisan.html", error=message, statuses_all=STATUS_ALL, default_statuses=["承認依頼中"],
        applied_from=dfrom.isoformat(), applied_to=dto.isoformat(),
    ), status


@bp.post("/seisan/stream")
def seisan_stream():
    lang = current_lang()
    try:
        params = parse_seisan(request.form)
    except ParamError as exc:
        return _sse_single_error(translate(str(exc), lang))
    if not playwright_available():
        return _sse_single_error(_localize_error(PlaywrightUnavailable(), lang))
    steps = [{"key": k, "label": translate(label, lang)} for k, label in SeisanClient.STEPS]

    def run(on_step):
        with browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
            client = SeisanClient(ctx, SEISAN_BASE_URL, log=lambda m, lvl="info": log.info("%s", m))
            try:
                name, data = client.download(params, on_step=on_step)
            finally:
                client.logout()
        return name, data, "text/csv"

    return _stream(steps, run, lang)


# ---------------------------------------------------------------------- 楽楽勤怠
@bp.get("/kintai")
def kintai_page():
    today = today_jst()
    return render_template(
        "kintai.html", error=None, company_default=KINTAI_COMPANY_DEFAULT,
        this_month=year_month(today), months_default=default_kintai_months(today),
    )


@bp.post("/kintai/run")
def kintai_run():
    try:
        params = parse_kintai(request.form, max_months=KINTAI_MAX_MONTHS)
    except ParamError as exc:
        return _kintai_error(translate(str(exc), current_lang()), 400)

    if not playwright_available():
        return _kintai_error(_localize_error(PlaywrightUnavailable()), 503)

    try:
        with browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
            client = KintaiClient(
                ctx, KINTAI_LOGIN_URL, KINTAI_ATTENDANCE_URL,
                log=lambda m, lvl="info": log.info("%s", m),
            )
            try:
                files = client.download(params)
            finally:
                client.logout()
    except RakuError as exc:
        log.warning("kintai failed: %s", exc.code)
        return _kintai_error(_localize_error(exc), 502)
    except Exception:  # noqa: BLE001
        log.exception("kintai unexpected error")
        return _kintai_error(translate("自動処理でエラーが発生しました。", current_lang()), 500)

    if len(files) == 1:
        name, data = files[0]
        return send_file(
            io.BytesIO(data), as_attachment=True, download_name=name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="出勤簿_日別詳細.zip", mimetype="application/zip")


def _kintai_error(message, status):
    today = today_jst()
    return render_template(
        "kintai.html", error=message, company_default=KINTAI_COMPANY_DEFAULT,
        this_month=year_month(today), months_default=default_kintai_months(today),
    ), status


@bp.post("/kintai/stream")
def kintai_stream():
    lang = current_lang()
    try:
        params = parse_kintai(request.form, max_months=KINTAI_MAX_MONTHS)
    except ParamError as exc:
        return _sse_single_error(translate(str(exc), lang))
    if not playwright_available():
        return _sse_single_error(_localize_error(PlaywrightUnavailable(), lang))
    steps = [{"key": k, "label": translate(label, lang)} for k, label in KintaiClient.steps_for(params.months)]

    def run(on_step):
        def localized_step(key, status, detail=None):
            # detail はクライアント側の日本語 (例: データ無しでスキップ) → 表示言語に翻訳。
            on_step(key, status, translate(detail, lang) if detail else None)

        with browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
            client = KintaiClient(
                ctx, KINTAI_LOGIN_URL, KINTAI_ATTENDANCE_URL, log=lambda m, lvl="info": log.info("%s", m)
            )
            try:
                files = client.download(params, on_step=localized_step)
            finally:
                client.logout()
        if len(files) == 1:
            name, data = files[0]
            return name, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files:
                zf.writestr(name, data)
        return "出勤簿_日別詳細.zip", buf.getvalue(), "application/zip"

    return _stream(steps, run, lang)
