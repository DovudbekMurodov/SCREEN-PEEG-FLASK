"""楽楽勤怠: ログイン → 出勤簿管理 → 各月の出勤簿(日別詳細)を取得して [(ファイル名, bytes)] を返す。"""
from __future__ import annotations

import os
import re
import tempfile
import time

from playwright.sync_api import Error as PlaywrightError

from rakuraku import selectors as sel
from rakuraku.errors import (
    AdditionalAuthRequired,
    DownloadTimeout,
    ExportFailed,
    LoginFailed,
    MonthNavigationFailed,
    NoDataFound,
    PasswordExpired,
    SelectorNotFound,
    SiteUnavailable,
)
from rakuraku.locators import exists, resolve, safe_click, set_checkbox, wait_for_any
from rakuraku.redact import register_secret

SERVICE = "kintai"

# 出勤簿グリッドを読み込む front-api (月別/日別リスト・絞り込み)。
LIST_API_RE = re.compile(r"attendanceManagement\.(getMonthlyList|getDailyList|filter)\b")


def _noop(*_a, **_k):
    return None


def _ym_index(ym):
    # type: (str) -> int
    y, m = ym.split("-")
    return int(y) * 12 + (int(m) - 1)


_COUNT_KEYS = ("total_count", "totalCount", "hit_count", "total")
_LIST_KEYS = ("list_data", "list", "rows", "items", "records", "employees")
_WRAP_KEYS = ("data", "result", "pager", "body", "payload")


def _list_count(obj, depth=0):
    # type: (object, int) -> int | None
    """getMonthlyList 応答から行数を推定する (0 = その月にデータ無し)。形が不明なら None。

    実応答 (画面JSより): res.data = {"data": {"list_data": [...行...], "pager": {"total_count": N, ...},
    "period_list": [...], "index": {...}, "filter": {...}, ...}}。行リスト/件数キーだけを見る。
    period_list 等の無関係なリストの長さを行数と誤認してはいけない (空の月を「行あり」と誤判定し、
    NoDataFound ではなく SelectorNotFound になってしまう)。
    """
    if not isinstance(obj, dict) or depth > 4:
        return None
    for key in _LIST_KEYS:
        v = obj.get(key)
        if isinstance(v, list):
            return len(v)
    for key in _COUNT_KEYS:
        v = obj.get(key)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    for key in _WRAP_KEYS:
        v = obj.get(key)
        if isinstance(v, dict):
            c = _list_count(v, depth + 1)
            if c is not None:
                return c
    return None


class KintaiClient:
    def __init__(self, context, login_url, attendance_url, log=_noop,
                 nav_timeout_ms=60000, export_timeout_s=300):
        self.context = context
        self.login_url = login_url
        self.attendance_url = attendance_url
        self.log = log
        self.nav_timeout_ms = nav_timeout_ms
        self.export_timeout_s = export_timeout_s
        self.page = None
        self.skipped = []  # データ無しでスキップした月
        self._last_list_count = None  # 直近の getMonthlyList 応答の行数 (不明なら None)
        self._debug_line = lambda line: None

    # ------------------------------------------------------------------ public
    def download(self, params, progress=None, on_step=None):
        # type: (object, object, object) -> list
        step = on_step or (lambda *a, **k: None)
        register_secret(params.password)
        try:
            step("login", "running")
            self._login(params.company_code, params.login_id, params.password)
            step("login", "done")
            step("open", "running")
            self._open_attendance()
            step("open", "done")
            results = []
            for ym in params.months:
                key = "export:%s" % ym
                step(key, "running")
                try:
                    self._ensure_month(ym)
                    self._require_grid(ym)
                    name, data = self._export(ym, progress)
                except NoDataFound:
                    # その月にデータが無い(サイトがグリッドを描画しない)。致命ではなく
                    # スキップして残りの月を続行。全月無しなら最後にエラーにする。
                    msg = "%s の出勤簿データがありません（この月はスキップしました）。" % ym
                    self.skipped.append(ym)
                    self.log(msg, "warning")
                    step(key, "skipped", msg)
                    continue
                results.append((name, data))
                step(key, "done")
            if not results:
                raise NoDataFound(
                    "no attendance data for %s" % ",".join(params.months), service=SERVICE,
                    user_message="選択した月の出勤簿データがありませんでした。",
                )
            return results
        except BaseException:
            from rakuraku.dump import dump

            dump(self.page, SERVICE, "fail")
            raise

    @staticmethod
    def steps_for(months):
        # type: (list) -> list
        base = [("login", "楽楽勤怠にログイン"), ("open", "出勤簿管理を開く")]
        return base + [("export:%s" % ym, "%s を出力" % ym) for ym in months]

    def logout(self):
        page = self.page
        if page is None or page.is_closed():
            return
        try:
            if exists(page, sel.kintai_logout(page)):
                resolve(page, sel.kintai_logout(page), timeout_ms=2000).click(timeout=5000)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ steps
    def _attach_debug(self, page):
        # RR_DEBUG_DUMP=1 のとき、/app 配下のAJAX応答とコンソール警告/エラーを
        # ファイルに残す(絞り込みが実際にAPIを叩いたか・0件か等の切り分け用)。
        from rakuraku.dump import enabled as _denabled

        if not _denabled():
            return
        import datetime as _dt
        out_dir = os.environ.get("RR_DEBUG_DIR") or os.path.join(os.getcwd(), "rakuraku_debug")
        try:
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "kintai_net_%s.log" % _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
            self._netlog = open(path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            return

        def _w(line):
            try:
                self._netlog.write("%.1f %s\n" % (time.monotonic(), line))
                self._netlog.flush()
            except Exception:  # noqa: BLE001
                pass

        def _keep(u):
            return ("front-api" in u) or (
                "/app/" in u and not u.endswith((".js", ".css", ".svg", ".png", ".woff", ".woff2", ".gif", ".ico"))
            )

        def on_req(r):
            try:
                if _keep(r.url):
                    _w("REQ  %s %s" % (r.method, r.url))
            except Exception:  # noqa: BLE001
                pass

        def on_resp(r):
            try:
                if _keep(r.url):
                    _w("RESP %s %s" % (r.status, r.url))
            except Exception:  # noqa: BLE001
                pass

        def on_failed(r):
            try:
                if _keep(r.url):
                    _w("FAIL %s %s" % (r.url, getattr(r, "failure", "")))
            except Exception:  # noqa: BLE001
                pass

        def on_console(m):
            try:
                if m.type in ("error", "warning"):
                    _w("CONSOLE[%s] %s" % (m.type, m.text[:300]))
            except Exception:  # noqa: BLE001
                pass

        self._debug_line = _w
        page.on("request", on_req)
        page.on("response", on_resp)
        page.on("requestfailed", on_failed)
        page.on("console", on_console)

    def _login(self, company, login_id, password):
        page = self.context.new_page()
        self.page = page
        self._attach_debug(page)
        page.on("response", self._on_list_response)
        try:
            page.goto(self.login_url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise SiteUnavailable(str(exc), service=SERVICE)

        resolve(page, sel.kintai_company(page), service=SERVICE, name="company").fill(company)
        resolve(page, sel.kintai_login_id(page), service=SERVICE, name="loginId").fill(login_id)
        resolve(page, sel.kintai_password(page), service=SERVICE, name="password").fill(password)
        if exists(page, sel.kintai_remember(page)):
            try:
                remember = resolve(page, sel.kintai_remember(page), timeout_ms=1000)
                if remember.is_checked():
                    remember.uncheck()
            except Exception:  # noqa: BLE001
                pass
        login_url = page.url
        safe_click(resolve(page, sel.kintai_submit(page), service=SERVICE, name="submit"), service=SERVICE)

        try:
            outcome = wait_for_any(
                page,
                {
                    "ok": lambda: "/app/login" not in page.url
                    and ("/app/" in page.url or exists(page, sel.kintai_app_header(page))),
                    "error": lambda: exists(page, sel.kintai_login_error(page)),
                    "expired": lambda: exists(page, sel.kintai_password_expired(page)),
                },
                timeout_ms=self.nav_timeout_ms,
            )
        except TimeoutError:
            if page.url == login_url and exists(page, sel.kintai_password(page)):
                raise LoginFailed("login form still shown", service=SERVICE)
            raise AdditionalAuthRequired("unexpected page: %s" % page.url, service=SERVICE)
        if outcome == "error":
            raise LoginFailed("login rejected", service=SERVICE)
        if outcome == "expired":
            raise PasswordExpired("password expired", service=SERVICE)

        try:
            resolve(page, sel.kintai_app_header(page), timeout_ms=10000)
        except SelectorNotFound:
            page.reload(wait_until="domcontentloaded")
            resolve(page, sel.kintai_app_header(page), timeout_ms=15000)
        self.log("楽楽勤怠にログインしました。")

    def _open_attendance(self):
        page = self.page
        try:
            page.goto(self.attendance_url, wait_until="domcontentloaded")
            self._wait_period(20000)
        except (PlaywrightError, MonthNavigationFailed):
            self.log("出勤簿管理のURLを直接開けなかったため、メニューから移動します。", "warning")
            safe_click(resolve(page, sel.kintai_menu_tab(page), service=SERVICE, name="menu"), service=SERVICE)
            safe_click(resolve(page, sel.kintai_menu_item(page), service=SERVICE, name="menu_item"), service=SERVICE)
            self._wait_period(self.nav_timeout_ms)
        # Vue アプリ(矢印/絞り込みのハンドラ)が完全にマウントされるまで待つ。
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightError:
            pass
        self.log("出勤簿管理を開きました（表示中: %s）。" % self._displayed_month())

    def _container_text(self):
        page = self.page
        try:
            container = page.locator(sel.KINTAI_PERIOD_CONTAINER)
            if container.count():
                return container.first.inner_text(timeout=3000)
            return page.locator("body").inner_text(timeout=3000)
        except PlaywrightError:
            return ""

    def _displayed_month(self):
        m = sel.KINTAI_PERIOD_RE.search(self._container_text())
        if not m:
            raise MonthNavigationFailed("period header not found")
        return "%04d-%02d" % (int(m.group(1)), int(m.group(2)))

    def _wait_period(self, timeout_ms):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            try:
                return self._displayed_month()
            except MonthNavigationFailed:
                if time.monotonic() >= deadline:
                    raise
                self.page.wait_for_timeout(300)

    def _click_and_wait_list(self, action, timeout_ms):
        # クリックで発火する出勤簿リストAPIの応答を待つ(遅い月読込を後続操作で
        # 中断させないため)。API応答が来れば True。
        page = self.page
        try:
            with page.expect_response(lambda r: bool(LIST_API_RE.search(r.url)),
                                      timeout=timeout_ms) as info:
                action()
            resp = info.value
            self._record_list_response(resp)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightError:
                pass
            return bool(getattr(resp, "ok", True))
        except (PlaywrightError, TimeoutError):
            return False

    def _record_list_response(self, resp):
        # getMonthlyList 応答の行数を控える。0 行 = その月はデータ無し (グリッド非描画)。
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            self._last_list_count = None
            self._debug_line("LIST body not JSON: %s" % getattr(resp, "url", "?"))
            return
        self._last_list_count = _list_count(body)
        keys = list(body.keys())[:12] if isinstance(body, dict) else type(body).__name__
        self._debug_line("LIST count=%s keys=%s" % (self._last_list_count, keys))

    def _on_list_response(self, resp):
        # 初回表示(goto)時の getMonthlyList も拾う受動リスナー。失敗しても無視。
        try:
            if LIST_API_RE.search(resp.url):
                self._record_list_response(resp)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_month(self, target):
        # 1クリック=1か月。対象月に届くまで進める。前月データの読込(AJAX)は
        # 実データだと十数秒かかることがあるため、月が変わるまで nav_timeout 待つ。
        guard = 0
        while True:
            current = self._displayed_month()
            if current == target:
                if guard:
                    self.log("対象月を%sに切り替えました。" % target)
                return
            guard += 1
            if guard > 18:
                raise MonthNavigationFailed("expected %s, stuck at %s" % (target, current), service=SERVICE)
            builder = sel.kintai_prev_month if _ym_index(target) < _ym_index(current) else sel.kintai_next_month
            try:
                arrow = resolve(self.page, builder(self.page), timeout_ms=8000, service=SERVICE, name="month_nav")
            except SelectorNotFound:
                raise MonthNavigationFailed(
                    "前月/翌月の切り替えボタンが見つかりません（要セレクタ確認）。", service=SERVICE)
            # 矢印クリックで発火する月リストAPIの応答を待つ(遅延読込が中断されないように)。
            # 前月の行数を持ち越さないよう先にリセット。読込の有無は _require_grid で判定。
            self._last_list_count = None
            self._click_and_wait_list(lambda: safe_click(arrow, service=SERVICE), timeout_ms=15000)
            self._wait_month_change(current, timeout_ms=self.nav_timeout_ms)

    def _wait_month_change(self, before, timeout_ms=15000):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                if self._displayed_month() != before:
                    return
            except MonthNavigationFailed:
                pass
            self.page.wait_for_timeout(300)
        raise MonthNavigationFailed("month did not change from %s" % before, service=SERVICE)

    def _icon_ready(self, timeout_ms):
        # Excelエクスポートアイコンはグリッド描画後にのみ存在する = グリッド読込完了の指標。
        try:
            resolve(self.page, sel.kintai_export_icon(self.page), timeout_ms=timeout_ms,
                    service=SERVICE, name="excel_icon")
            return True
        except SelectorNotFound:
            return False

    def _require_grid(self, ym):
        # 矢印クリックで月リストAPI(getMonthlyList)が自動発火し、行があればグリッドと
        # Excelアイコンが描画される。行が0件だとサイトはグリッドを一切描画しない
        # (ページャも見出しも無い) ため、アイコン不在 + API 0件 = その月はデータ無し。
        if self._icon_ready(8000):
            return
        from rakuraku.dump import dump, enabled as _denabled

        if _denabled():
            dump(self.page, SERVICE, "nogrid_%s" % ym.replace("-", ""))
        count = self._last_list_count
        if count == 0:
            raise NoDataFound("list API returned 0 rows for %s" % ym, service=SERVICE)
        # 行はあるはずなのにアイコンが無い = 画面構成の変化 (セレクタ要確認)。
        raise SelectorNotFound(
            "excel_icon: grid not rendered for %s (list count=%s)" % (ym, count), service=SERVICE)

    def _export(self, ym, progress):
        page = self.page
        safe_click(resolve(page, sel.kintai_export_icon(page), service=SERVICE, name="excel_icon"), service=SERVICE)
        # ダイアログ枠 (.dialog_window) は子要素が fixed 配置でサイズ0のため可視判定にならない。
        # スコープとして使うだけなので visible=False で取得する。
        dialog = resolve(page, sel.kintai_export_dialog(page), service=SERVICE, name="dialog", visible=False)
        # 「日別詳細」= 非表示の radio(value=2)。可視ラベルをクリックして Vue に選択させ、
        # 保険として set_checkbox も実行。最終的な形式はダウンロードファイル名(月別/日別)で検証する。
        try:
            safe_click(resolve(dialog, sel.kintai_daily_label(dialog), timeout_ms=3000, service=SERVICE, name="daily_label"),
                       service=SERVICE)
        except SelectorNotFound:
            pass
        try:
            radio = resolve(dialog, sel.kintai_daily_radio(dialog), timeout_ms=3000, service=SERVICE, name="daily_radio", visible=False)
            set_checkbox(radio, True)
        except SelectorNotFound:
            self.log("日別詳細ラジオが見つかりませんでした（ラベルクリックで選択済みの想定）。", "warning")
        safe_click(resolve(dialog, sel.kintai_export_button(dialog), service=SERVICE, name="export_btn"), service=SERVICE)

        modal = resolve(page, sel.kintai_progress_modal(page), timeout_ms=30000, service=SERVICE, name="progress", visible=False)
        button = self._wait_ready(modal, ym, progress)
        downloads = []

        def _on_download(d):
            downloads.append(d)

        page.on("download", _on_download)
        try:
            safe_click(button, service=SERVICE)
            wait_for_any(page, {"download": lambda: bool(downloads)}, timeout_ms=120000)
        except TimeoutError:
            raise DownloadTimeout("download for %s did not start" % ym, service=SERVICE)
        finally:
            try:
                page.remove_listener("download", _on_download)
            except Exception:  # noqa: BLE001
                pass
        download = downloads[0]
        if download.failure():
            raise ExportFailed("download failed: %s" % download.failure(), service=SERVICE)
        name = os.path.basename(download.suggested_filename or ("出勤簿_日別詳細_%s.xlsx" % ym))
        if "月別" in name:
            raise ExportFailed("monthly format downloaded instead of daily", service=SERVICE)
        data = _read_download(download)
        if data[:4] != b"PK\x03\x04":
            raise ExportFailed("downloaded file is not xlsx", service=SERVICE)
        try:
            safe_click(resolve(page, sel.kintai_close_button(page), timeout_ms=5000), service=SERVICE)
            modal.wait_for(state="hidden", timeout=10000)
        except (SelectorNotFound, PlaywrightError):
            self.log("エクスポート画面を閉じられませんでした（処理は継続します）。", "warning")
        self.log("出勤簿（%s）をダウンロードしました: %s" % (ym, name))
        return "%s_%s" % (ym, name), data

    def _wait_ready(self, modal, ym, progress):
        started = time.monotonic()
        last = -10.0
        while True:
            elapsed = time.monotonic() - started
            try:
                text = modal.inner_text(timeout=5000)
            except PlaywrightError:
                text = ""
            if sel.KINTAI_DONE_TEXT.search(text):
                try:
                    # モーダル枠はサイズ0のため visible=False。ボタン自体は表示・活性を別途確認。
                    button = resolve(modal, sel.kintai_download_button(modal), timeout_ms=2000, visible=False)
                    if button.is_visible() and button.is_enabled():
                        return button
                except SelectorNotFound:
                    pass
            elif sel.KINTAI_FAILED_TEXT.search(text):
                raise ExportFailed("export failed: %r" % text[:120], service=SERVICE)
            if elapsed > self.export_timeout_s:
                raise DownloadTimeout("export not finished after %ds" % self.export_timeout_s, service=SERVICE)
            if progress and elapsed - last >= 5:
                progress("処理完了待ち %d秒" % int(elapsed))
                last = elapsed
            self.page.wait_for_timeout(1000)


def _read_download(download):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp.close()
    try:
        download.save_as(tmp.name)
        with open(tmp.name, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
