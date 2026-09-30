"""楽楽精算 / 楽楽勤怠 ダウンロード機能の Flask Blueprint。

既存の app.py には登録の1行だけを足す。サーバにデータは保存しない:
認証情報はフォームで受け取り、メモリ上で Playwright に渡し、生成物は
一時ファイル→レスポンス配信後に破棄する。
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import os
import queue
import threading
import time
import zipfile

from flask import Blueprint, Response, current_app, render_template, request, send_file

from i18n import current_lang, translate
from rakuraku import redact
from rakuraku.browser import browser_session, playwright_available
from rakuraku.errors import (
    SERVICE_NAMES,
    BrowserFailed,
    JobCancelled,
    PlaywrightUnavailable,
    RakuError,
    ServerBusy,
)
from rakuraku.kintai import KintaiClient
from rakuraku.locators import PlaywrightError, set_cancel
from rakuraku.oneclick import (
    mime_of,
    months_from_expense_csv,
    normalize_attendance_xlsx,
    plan_kintai_months,
    run_engine,
)
from rakuraku.params import (
    SCOPES,
    KintaiParams,
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

# 同時に動かすブラウザ処理の数。1 GB のサーバでは Chromium 2つでメモリ上限を超えるため既定 1。
# 空きが無いときは順番待ち (画面に「お待ちください」) し、QUEUE_WAIT_SECONDS を超えたら諦める。
MAX_CONCURRENT_JOBS = max(1, int(os.environ.get("RR_MAX_CONCURRENT_JOBS", "1") or 1))
QUEUE_WAIT_SECONDS = int(os.environ.get("RR_QUEUE_WAIT_SECONDS", "900") or 900)
HEARTBEAT_SECONDS = 15  # 無通信で社内プロキシ等に切られないよう、SSE にコメント行を送る間隔
WAIT_POLL_SECONDS = 5  # 順番待ち中に「お待ちください」を送り直す間隔
# 順番待ちできる人数。待つ間も gunicorn のスレッドを1つ使うため、スレッド数より十分少なくし、
# 超えたらすぐ「混み合っています」を返す (待ちでページ表示まで止まらないように)。
MAX_WAITING_JOBS = int(os.environ.get("RR_MAX_WAITING_JOBS", "3") or 3)
# ストリームを使わない旧フォーム送信 (JS無効時) は nginx の 600 秒を超えないよう短く待つ。
NON_STREAM_WAIT_SECONDS = 60
_job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
_waiting = [0]
_waiting_lock = threading.Lock()


@contextlib.contextmanager
def _job_slot(on_wait=None, max_wait=None):
    # type: (object, float | None) -> object
    """ブラウザ処理の実行枠を1つ確保する。待つ間は on_wait() を WAIT_POLL_SECONDS ごとに呼ぶ。"""
    if not _job_slots.acquire(blocking=False):
        with _waiting_lock:
            if _waiting[0] >= MAX_WAITING_JOBS:
                raise ServerBusy("queue full")
            _waiting[0] += 1
        try:
            deadline = time.monotonic() + (QUEUE_WAIT_SECONDS if max_wait is None else max_wait)
            while True:
                if on_wait:
                    on_wait()
                if _job_slots.acquire(timeout=WAIT_POLL_SECONDS):
                    break
                if time.monotonic() >= deadline:
                    raise ServerBusy("no free job slot")
        finally:
            with _waiting_lock:
                _waiting[0] -= 1
    try:
        yield
    finally:
        _job_slots.release()


def _localize_error(exc, lang=None):
    # type: (RakuError, str | None) -> str
    lang = lang or current_lang()
    template = getattr(exc, "template", None) or getattr(exc, "user_message", "")
    text = translate(template, lang)
    name = SERVICE_NAMES.get(exc.service) if exc.service else "楽楽精算・楽楽勤怠"
    if "{service}" in text:
        text = text.format(service=name)
    shown = getattr(exc, "site_message", "")
    if shown:  # 楽楽側の実際の表示を添える (IDとパスワードの誤りか等を画面で判断できるように)
        text += translate("（{service}の表示：「{msg}」）", lang).format(service=name, msg=shown)
    return text


# ------------------------------------------------------------ streaming (live steps)
def _sse(event, obj):
    # type: (str, dict) -> str
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(obj, ensure_ascii=False))


def _stream(steps, run, lang, secrets=()):
    # type: (list, object, str, tuple) -> Response
    """run(on_step) をワーカースレッドで実行し、ステップ進捗を SSE で流す。サーバに保存しない。

    run の戻り値が (filename, bytes, mime) なら最後にファイルを base64 で送る。
    {"event": 名前, "data": dict} ならそのイベントを送る (ワンクリック実行の結果など)。
    on_step.emit(event, data) で任意のイベント (ステップ追加など) も送れる。"""

    def gen():
        q = queue.Queue()
        holder = {}
        cancel = threading.Event()  # 接続が切れたら立てる → 次のステップ境界で打ち切り

        def on_step(key, status, detail=None):
            if cancel.is_set():
                raise JobCancelled("client disconnected")
            q.put(("step", {"key": key, "status": status, "detail": detail}))

        on_step.emit = lambda event, data: q.put((event, data))

        def on_wait():
            if cancel.is_set():
                raise JobCancelled("client disconnected while waiting")
            q.put(("wait", {"message": translate("ほかの方の処理が終わるまでお待ちください…", lang)}))

        def work():
            # secrets: このジョブの間だけ、ログ (例外のトレースバック含む) でパスワードを伏せる
            set_cancel(cancel)  # 長い待ちの途中でも、接続が切れたら打ち切れるように
            try:
                with redact.secrets(*secrets):
                    _work()
            finally:
                set_cancel(None)

        def _work():
            try:
                with _job_slot(on_wait):
                    out = run(on_step)
                if isinstance(out, dict):
                    q.put((out["event"], out["data"]))
                else:
                    holder["file"] = out
                    q.put(("done", None))
            except JobCancelled:
                log.info("stream cancelled (client disconnected)")
            except RakuError as exc:
                log.warning("stream failed: %s", getattr(exc, "code", "?"))
                q.put(("error", {"message": _localize_error(exc, lang), "log": getattr(exc, "log", "") or ""}))
            except PlaywrightError:
                log.exception("stream browser error")
                q.put(("error", {"message": _localize_error(BrowserFailed(), lang), "log": ""}))
            except Exception:  # noqa: BLE001
                log.exception("stream unexpected error")
                q.put(("error", translate("自動処理でエラーが発生しました。", lang)))
            finally:
                q.put((None, None))

        threading.Thread(target=work, daemon=True).start()
        try:
            yield _sse("steps", {"steps": steps})
            while True:
                try:
                    typ, payload = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if typ is None:
                    break
                if typ == "done":
                    name, data, mime = holder["file"]
                    yield _sse("file", {"filename": name, "mime": mime, "b64": base64.b64encode(data).decode()})
                elif typ == "error":
                    yield _sse("error", payload if isinstance(payload, dict) else {"message": payload})
                else:
                    yield _sse(typ, payload)
        finally:
            cancel.set()  # 正常終了でも立ててよい (処理はもう終わっている)

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
        with redact.secrets(params.password), _job_slot(max_wait=NON_STREAM_WAIT_SECONDS), \
                browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
            client = SeisanClient(ctx, SEISAN_BASE_URL, log=lambda m, lvl="info": log.info("%s", m))
            try:
                name, data = client.download(params)
            finally:
                client.logout()
    except RakuError as exc:
        log.warning("seisan failed: %s", exc.code)
        return _seisan_error(_localize_error(exc), 502)
    except PlaywrightError:
        log.exception("seisan browser error")
        return _seisan_error(_localize_error(BrowserFailed()), 502)
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

    return _stream(steps, run, lang, secrets=(params.password,))


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
        with redact.secrets(params.password), _job_slot(max_wait=NON_STREAM_WAIT_SECONDS), \
                browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
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
    except PlaywrightError:
        log.exception("kintai browser error")
        return _kintai_error(_localize_error(BrowserFailed()), 502)
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

    return _stream(steps, run, lang, secrets=(params.password,))


# ---------------------------------------------------------------------- ワンクリック実行
# 楽楽精算の出力 → 楽楽勤怠の出力 → チェックシート作成 を1回のクリックで行う。
ONECLICK_MAX_MONTHS = int(os.environ.get("RR_ONECLICK_MAX_MONTHS", "4") or 4)
ONECLICK_CHECK_STEP = "check"


def _engine():
    # app.py が渡す既存チェックシート処理の入口 (未登録なら None)。
    return current_app.config.get("CHECKSHEET_ENGINE")


def _prefixed(form, prefix):
    # type: (object, str) -> dict
    """ワンクリック画面の s_* / k_* を、それぞれ parse_seisan / parse_kintai 用の dict にする。"""
    out = {}
    for key in form.keys():
        if key.startswith(prefix):
            values = form.getlist(key)
            out[key[len(prefix):]] = values if len(values) > 1 else values[0]
    return out


def _b64_file(name, data):
    # type: (str, bytes) -> dict
    return {"filename": name, "mime": mime_of(name), "b64": base64.b64encode(data).decode()}


@bp.get("/oneclick")
def oneclick_page():
    dfrom, dto = default_seisan_range()
    engine = _engine()
    return render_template(
        "oneclick.html", error=None, company_default=KINTAI_COMPANY_DEFAULT,
        applied_from=dfrom.isoformat(), applied_to=dto.isoformat(),
        approvers=engine.approvers() if engine else [], max_months=ONECLICK_MAX_MONTHS,
        scopes=SCOPES, statuses_all=STATUS_ALL, default_statuses=["承認依頼中"],
    )


@bp.post("/oneclick/stream")
def oneclick_stream():
    lang = current_lang()
    today = today_jst()
    try:
        sparams = parse_seisan(_prefixed(request.form, "s_"), today=today)
        kcred = parse_kintai(_prefixed(request.form, "k_"), today=today)
    except ParamError as exc:
        return _sse_single_error(translate(str(exc), lang))
    engine = _engine()
    if engine is None:
        return _sse_single_error(translate("自動処理でエラーが発生しました。", lang))
    if not playwright_available():
        return _sse_single_error(_localize_error(PlaywrightUnavailable(), lang))
    approver = (request.form.get("approver") or "").strip()
    if approver and approver not in engine.approvers():
        approver = ""  # 未知の氏名は絞り込みなし (/run と同じ扱い)

    # 出勤簿の月は楽楽精算CSVを取得してから決まるため、月別ステップは後から挿入する。
    steps = (
        [{"key": "s:" + k, "label": translate(label, lang)} for k, label in SeisanClient.STEPS]
        + [{"key": "k:" + k, "label": translate(label, lang)} for k, label in KintaiClient.steps_for([])]
        + [{"key": ONECLICK_CHECK_STEP, "label": translate("チェックシートを作成", lang)}]
    )

    def run(on_step):
        def step_for(prefix):
            def _step(key, status, detail=None):
                on_step(prefix + key, status, translate(detail, lang) if detail else None)
            return _step

        info = lambda m, lvl="info": log.info("%s", m)  # noqa: E731
        with browser_session(headless=HEADLESS, slow_mo_ms=SLOWMO_MS, proxy_url=PROXY_URL) as ctx:
            seisan = SeisanClient(ctx, SEISAN_BASE_URL, log=info)
            try:
                csv_name, csv_data = seisan.download(sparams, on_step=step_for("s:"))
            finally:
                seisan.logout()

            # 当月・前月は必ず取得し、CSVの明細日付にそれより前の月があれば追加する。
            months, dropped = plan_kintai_months(
                months_from_expense_csv(csv_data), default_kintai_months(today), ONECLICK_MAX_MONTHS)
            on_step.emit("steps", {"before": ONECLICK_CHECK_STEP, "steps": [
                {"key": "k:" + k, "label": translate(label, lang)}
                for k, label in KintaiClient.steps_for(months)[2:]
            ]})

            kintai = KintaiClient(ctx, KINTAI_LOGIN_URL, KINTAI_ATTENDANCE_URL, log=info)
            try:
                att_files = kintai.download(
                    KintaiParams(kcred.company_code, kcred.login_id, kcred.password, months),
                    on_step=step_for("k:"),
                )
            finally:
                kintai.logout()
            skipped = list(kintai.skipped)

        # ブラウザを閉じてからエンジンを動かす (小さいサーバでメモリを空けるため)。
        on_step(ONECLICK_CHECK_STEP, "running")
        attendance = [(name[:7], normalize_attendance_xlsx(data)) for name, data in att_files]
        result = run_engine(engine, csv_data, attendance, approver)
        on_step(ONECLICK_CHECK_STEP, "done")
        return {"event": "result", "data": {
            "summary": [{"label": label, "text": translate(label, lang), "count": count}
                        for label, count in result["summary"]],
            "files": [_b64_file(n, d) for n, d in result["files"]],
            "sources": [_b64_file(csv_name, csv_data)] + [_b64_file(n, d) for n, d in att_files],
            "log": result["log"],
            "skipped": skipped,
            "dropped": dropped,
            "approver": approver or translate("全員（絞り込みなし）", lang),
        }}

    return _stream(steps, run, lang, secrets=(sparams.password, kcred.password))
