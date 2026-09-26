"""楽楽精算: ログイン → 伝票データ出力 → 出張精算CSV を取得して (ファイル名, bytes) を返す。"""
from __future__ import annotations

import os
import tempfile

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PWTimeout
except ImportError:  # playwright 未インストールでも import 可能にする (実行時は playwright_available で拒否)
    from rakuraku.locators import PlaywrightError  # type: ignore[assignment]

    class PWTimeout(PlaywrightError):  # type: ignore[no-redef]
        pass

from rakuraku import selectors as sel
from rakuraku.errors import (
    AdditionalAuthRequired,
    DownloadTimeout,
    ExportFailed,
    LoginFailed,
    NoDataFound,
    PasswordExpired,
    SelectorNotFound,
    SiteUnavailable,
)
from rakuraku.locators import (
    exists,
    nfkc,
    resolve,
    safe_click,
    select_option_fuzzy,
    set_checkbox as _set_checkbox,
    wait_for_any,
)
from rakuraku.redact import register_secret

SERVICE = "seisan"


def _noop(*_a, **_k):
    return None


class SeisanClient:
    def __init__(self, context, base_url, log=_noop, nav_timeout_ms=60000, download_timeout_ms=180000):
        self.context = context
        self.base_url = base_url.rstrip("/") + "/"
        self.log = log
        self.nav_timeout_ms = nav_timeout_ms
        self.download_timeout_ms = download_timeout_ms
        self.page = None

    STEPS = [("login", "楽楽精算にログイン"), ("export", "抽出条件を設定"), ("download", "CSVをダウンロード")]

    # ------------------------------------------------------------------ public
    def download(self, params, on_step=None):
        # type: (object, object) -> tuple[str, bytes]
        step = on_step or (lambda *a, **k: None)
        register_secret(params.password)
        try:
            step("login", "running")
            self._login(params.login_id, params.password)
            step("login", "done")
            step("export", "running")
            page = self._open_export(params.scope)
            self._configure(page, params)
            step("export", "done")
            step("download", "running")
            result = self._download_csv(page)
            step("download", "done")
            return result
        except BaseException:
            from rakuraku.dump import dump

            dump(self.page, SERVICE, "fail")
            raise

    def logout(self):
        page = self.page
        if page is None or page.is_closed():
            return
        try:
            if exists(page, sel.seisan_logout(page)):
                resolve(page, sel.seisan_logout(page), timeout_ms=2000).click(timeout=5000)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ steps
    def _login(self, login_id, password):
        page = self.context.new_page()
        self.page = page
        self._watch_dialogs(page)
        try:
            page.goto(self.base_url + sel.SEISAN_LOGIN_PATH, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise SiteUnavailable(str(exc), service=SERVICE)

        resolve(page, sel.seisan_login_id(page), service=SERVICE, name="loginId").fill(login_id)
        resolve(page, sel.seisan_password(page), service=SERVICE, name="password").fill(password)
        login_url = page.url
        safe_click(resolve(page, sel.seisan_submit(page), service=SERVICE, name="submit"), service=SERVICE)

        try:
            outcome = wait_for_any(
                page,
                {
                    "ok": lambda: sel.SEISAN_TOP_HINT in page.url or exists(page, _any_export_link(page)),
                    "error": lambda: exists(page, sel.seisan_login_error(page)),
                    "expired": lambda: exists(page, sel.seisan_password_expired(page)),
                },
                timeout_ms=self.nav_timeout_ms,
            )
        except TimeoutError:
            if page.url == login_url and exists(page, sel.seisan_password(page)):
                raise LoginFailed("login form still shown", service=SERVICE)
            raise AdditionalAuthRequired("unexpected page: %s" % page.url, service=SERVICE)
        if outcome == "error":
            raise LoginFailed("login rejected", service=SERVICE)
        if outcome == "expired":
            raise PasswordExpired("password expired", service=SERVICE)
        self.log("楽楽精算にログインしました。")

    def _open_export(self, scope):
        page = self.page
        path = sel.SEISAN_EXPORT_PATH[scope]
        try:
            page.goto(self.base_url + path, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise SiteUnavailable(str(exc), service=SERVICE)
        try:
            wait_for_any(
                page,
                {"heading": lambda: bool(sel.SEISAN_EXPORT_HEADING.search(_page_title(page)))
                    or exists(page, sel.seisan_category_select(page))},
                timeout_ms=self.nav_timeout_ms,
            )
        except TimeoutError:
            raise SelectorNotFound("export screen not shown", service=SERVICE)
        self.log("伝票データ出力（%s）画面を開きました。" % scope)
        return page

    def _configure(self, page, params):
        # カテゴリ選択 (変更で changeKbn リロードが走ることがある)
        select = resolve(page, sel.seisan_category_select(page), service=SERVICE, name="category")
        current = _selected_label(select)
        if nfkc(params.category) not in nfkc(current):
            try:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=self.nav_timeout_ms):
                    chosen = select_option_fuzzy(select, params.category)
            except PWTimeout:
                chosen = _selected_label(resolve(page, sel.seisan_category_select(page)))
            self.log("伝票データ出力を「%s」に設定しました。" % chosen)

        # 項目名を出力する (szb-checkbox はカスタム部品なので check() では状態が変わらない)
        checkbox = resolve(page, sel.seisan_item_names_checkbox(page), service=SERVICE, name="item_names")
        _set_checkbox(checkbox, True)

        # 申請日 (from Y/M/D, to Y/M/D)
        self._fill_date_range(page, params.applied_from, params.applied_to)

        # 伝票状態: 選んだものだけ ON
        wanted = set(params.statuses)
        for label in sel.SEISAN_STATUS_ORDER:
            status_id = sel.SEISAN_STATUS_IDS[label]
            box = page.locator('[id="%s"]' % status_id)
            if box.count() == 0:
                continue
            _set_checkbox(box.first, label in wanted)
        self.log(
            "出力条件: %s / 申請日 %s〜%s / 伝票状態 %s"
            % (params.category, params.applied_from, params.applied_to, ",".join(params.statuses))
        )

    def _fill_date_range(self, page, dfrom, dto):
        values = [
            "%04d" % dfrom.year, "%02d" % dfrom.month, "%02d" % dfrom.day,
            "%04d" % dto.year, "%02d" % dto.month, "%02d" % dto.day,
        ]
        # 申請日ラベル直後の 6 個の日付入力を優先 (承認完了日と混同しないため)
        scoped = page.locator(
            "xpath=(//*[normalize-space(text())='申請日']/following::input[contains(@class,'positiveIntTextBox')])[position()<=6]"
        )
        try:
            count = scoped.count()
        except PlaywrightError:
            count = 0
        if count == 6:
            inputs = scoped
        else:
            inputs = resolve(page, [sel.seisan_date_inputs(page)[0]], service=SERVICE, name="date_inputs")
            inputs = page.locator("input.positiveIntTextBox.imeOff.d_widthTxt4")
            if inputs.count() < 6:
                raise SelectorNotFound("申請日 date inputs not found", service=SERVICE)
            if inputs.count() != 6:
                self.log("申請日の入力欄を先頭6個と仮定しました（要確認）。", "warning")
        for i, value in enumerate(values):
            field = inputs.nth(i)
            try:
                field.click()
                field.fill(value)
            except PlaywrightError:
                pass
            # 値が入らない/独自入力欄の場合は JS で直接セット
            try:
                if (field.input_value() or "").strip() != value:
                    field.evaluate(
                        "(el, v) => { el.value = v;"
                        " el.dispatchEvent(new Event('input', {bubbles:true}));"
                        " el.dispatchEvent(new Event('change', {bubbles:true})); }",
                        value,
                    )
            except PlaywrightError:
                pass

    def _download_csv(self, page):
        button = resolve(page, sel.seisan_file_output(page), service=SERVICE, name="file_output")
        downloads = []

        def _on_download(d):
            downloads.append(d)

        page.on("download", _on_download)
        try:
            safe_click(button, service=SERVICE)
            outcome = wait_for_any(
                page,
                {"download": lambda: bool(downloads), "no_data": lambda: self._no_data(page)},
                timeout_ms=self.download_timeout_ms,
            )
        except TimeoutError:
            raise DownloadTimeout("CSV download did not start", service=SERVICE)
        finally:
            try:
                page.remove_listener("download", _on_download)
            except Exception:  # noqa: BLE001
                pass
        if outcome == "no_data" and not downloads:
            raise NoDataFound("no vouchers matched", service=SERVICE)
        download = downloads[0]
        if download.failure():
            raise ExportFailed("download failed: %s" % download.failure(), service=SERVICE)
        name = os.path.basename(download.suggested_filename or "出張精算.csv")
        data = _read_download(download)
        _validate_csv(data)
        self.log("CSVをダウンロードしました: %s（%d bytes）" % (name, len(data)))
        return name, data

    # ------------------------------------------------------------------ helpers
    def _watch_dialogs(self, page):
        import re as _re

        def handle(dialog):
            try:
                if dialog.type == "confirm" and _re.search(r"出力|ダウンロード|よろしい", dialog.message):
                    dialog.accept()
                else:
                    dialog.dismiss()
            except Exception:  # noqa: BLE001
                pass

        page.on("dialog", handle)

    def _no_data(self, page):
        try:
            body = page.locator("body").inner_text(timeout=1500)
        except PlaywrightError:
            return False
        return bool(sel.SEISAN_NO_DATA.search(body or ""))


def _any_export_link(page):
    import re as _re

    return [lambda s: s.get_by_role("link", name=_re.compile(r"伝票データ出力"))]


def _page_title(page):
    try:
        return "%s %s" % (page.title(), page.locator("h1,h2,.title").first.inner_text(timeout=500))
    except PlaywrightError:
        try:
            return page.title()
        except PlaywrightError:
            return ""


def _selected_label(select):
    try:
        return select.locator("option:checked").first.inner_text(timeout=2000)
    except PlaywrightError:
        return ""


def _read_download(download):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
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


def _validate_csv(data):
    if not data:
        raise ExportFailed("downloaded CSV is empty", service=SERVICE)
    head = ""
    for enc in ("cp932", "utf-8-sig"):
        try:
            head = data[:4096].decode(enc, "strict").splitlines()[0]
            break
        except (UnicodeDecodeError, IndexError):
            head = ""
    if "伝票No" not in nfkc(head) and "伝票番号" not in nfkc(head):
        raise ExportFailed("unexpected CSV header: %r" % head[:80], service=SERVICE)
