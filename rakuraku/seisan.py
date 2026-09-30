"""楽楽精算: ログイン → 伝票データ出力 → 出張精算CSV を取得して (ファイル名, bytes) を返す。"""
from __future__ import annotations

import os
import re
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
    AccountLocked,
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
    ERRORISH,
    exists,
    first_text,
    nfkc,
    resolve,
    safe_click,
    select_option_fuzzy,
    set_checkbox as _set_checkbox,
    visible_messages,
    wait_for_any,
)
from rakuraku.redact import redact, register_secret, unregister_secret

SERVICE = "seisan"

# 「ファイル出力」後に楽楽側のメッセージ (該当なし等) が出たら、念のためこの秒数だけ
# ダウンロード開始を待ってから結果を判定する (お知らせだけでダウンロードは続く場合に備える)。
SIGNAL_GRACE_MS = 5000


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
        self.dialogs = []  # [(type, message)] ページが出したダイアログ (alert/confirm)

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
        finally:
            unregister_secret(params.password)

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
            # ログイン画面が(URLが変わっても)まだ出ている = 認証に失敗。追加認証とは区別する。
            if exists(page, sel.seisan_password(page)):
                raise LoginFailed("login form still shown", service=SERVICE,
                                  site_message=_errorish(visible_messages(page)))
            raise AdditionalAuthRequired("unexpected page: %s" % page.url, service=SERVICE)
        if outcome == "error":
            shown = redact(first_text(page, sel.seisan_login_error(page)))
            if "ロック" in shown:
                raise AccountLocked("account locked", service=SERVICE, site_message=shown)
            raise LoginFailed("login rejected", service=SERVICE, site_message=shown)
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
            except SelectorNotFound as exc:
                raise SelectorNotFound(
                    str(exc), service=SERVICE,
                    user_message="楽楽精算の伝票データ出力に、指定した種類が見つかりませんでした。種類の指定をご確認ください。",
                )
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

        def _on_page(p):  # 別ウィンドウでダウンロードされる設定にも対応
            p.on("download", _on_download)

        # クリック前の表示を控え、クリック後に「新しく出た」文言だけを判定に使う
        # (画面に常時ある説明文を「該当なし」と誤判定しないため)。
        before_msgs = set(visible_messages(page))
        before_nodata = self._no_data_lines(page)
        mark = len(self.dialogs)

        def signal():
            # 楽楽側が出した「該当なし/エラー」の文言。無ければ ""。
            for typ, msg in self.dialogs[mark:]:
                if typ != "confirm" and msg:
                    return msg
            new_nodata = self._no_data_lines(page) - before_nodata
            if new_nodata:
                return sorted(new_nodata)[0]
            return _errorish(m for m in visible_messages(page) if m not in before_msgs)

        page.on("download", _on_download)
        self.context.on("page", _on_page)
        try:
            safe_click(button, service=SERVICE)
            try:
                outcome = wait_for_any(
                    page,
                    {"download": lambda: bool(downloads), "signal": lambda: bool(signal())},
                    timeout_ms=self.download_timeout_ms,
                )
            except TimeoutError:
                shown = _errorish(m for m in visible_messages(page) if m not in before_msgs)
                raise DownloadTimeout(
                    "CSV download did not start", service=SERVICE, site_message=redact(shown),
                    user_message="楽楽精算からCSVが出力されませんでした。指定した条件（申請日・伝票状態）に"
                                 "該当する伝票がない可能性があります。条件を変えて再度お試しください。",
                )
            if outcome == "signal" and not downloads:
                shown = redact(signal())
                try:
                    wait_for_any(page, {"download": lambda: bool(downloads)}, timeout_ms=SIGNAL_GRACE_MS)
                except TimeoutError:
                    if sel.SEISAN_NO_DATA.search(shown):
                        raise NoDataFound(
                            "no vouchers matched", service=SERVICE, site_message=shown,
                            user_message="楽楽精算に、指定した条件（申請日・伝票状態）に該当する伝票がありませんでした。"
                                         "条件を変えて再度お試しください。",
                        )
                    raise ExportFailed("site message: %s" % shown, service=SERVICE, site_message=shown)
        finally:
            for target, event, fn in ((page, "download", _on_download), (self.context, "page", _on_page)):
                try:
                    target.remove_listener(event, fn)
                except Exception:  # noqa: BLE001
                    pass
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
                self.dialogs.append((dialog.type, re.sub(r"\s+", " ", dialog.message or "").strip()[:200]))
            except Exception:  # noqa: BLE001
                pass
            try:
                if dialog.type == "confirm" and _re.search(r"出力|ダウンロード|よろしい", dialog.message):
                    dialog.accept()
                else:
                    dialog.dismiss()
            except Exception:  # noqa: BLE001
                pass

        page.on("dialog", handle)

    def _no_data_lines(self, page):
        # type: (object) -> set
        try:
            body = page.locator("body").inner_text(timeout=1500)
        except PlaywrightError:
            return set()
        return {line.strip()[:200] for line in (body or "").splitlines() if sel.SEISAN_NO_DATA.search(line)}


def _errorish(texts):
    # type: (object) -> str
    """文言のうち「失敗・該当なし」を示す最初のもの。無ければ ""。"""
    for text in texts:
        if ERRORISH.search(text or ""):
            return text
    return ""


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
    # 見出し行だけを見る (先頭 N バイトで切ると多バイト文字の途中で切れて誤判定するため)。
    # cp932 / UTF-8 のどちらかで「伝票No」を含めば正しいCSVとみなす。
    first = data.split(b"\n", 1)[0].rstrip(b"\r")
    heads = []
    for enc in ("cp932", "utf-8-sig"):
        try:
            heads.append(nfkc(first.decode(enc, "strict")))
        except UnicodeDecodeError:
            continue
    if not any("伝票No" in h or "伝票番号" in h for h in heads):
        raise ExportFailed("unexpected CSV header: %r" % first[:80], service=SERVICE)
