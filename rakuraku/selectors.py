"""楽楽精算 / 楽楽勤怠 のセレクタを1か所に集約。

Seisan は rakusensai (1).txt の PAD コントロールリポジトリ由来の実IDを使用。
Kintai は Kintai (1).txt 由来 (ラベルに U+200B を含むため exact 一致は使わない)。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 楽楽精算 (Seisan)
SEISAN_LOGIN_PATH = "ssooff"  # base_url + "ssooff"
SEISAN_EXPORT_PATH = {
    "自部門": "sapDcsvoutJiBumonDownload/initializeView",
    "全部門": "sapDcsvoutZenBumonDownload/initializeView",
}
SEISAN_TOP_HINT = "sapTopPage"
SEISAN_EXPORT_HEADING = re.compile(r"伝票データ出力")
# 「ファイル出力」で該当伝票が無いときの表示。文言の揺れ (出力対象のデータが存在しません /
# 該当する伝票がありません / 対象データはありません 等) を広く拾う。行単位で判定する。
SEISAN_NO_DATA = re.compile(
    r"(対象|該当|出力)[^\n。]{0,12}(データ|伝票|明細)[^\n。]{0,8}(ありません|存在しません|見つかりません)"
    r"|(データ|伝票|明細)(が|は)(ありません|存在しません|見つかりません)"
    r"|該当する[^\n]*ありません|(?<![0-9０-９])[0０]件"
)

# 伝票状態チェックボックスの id (PAD 由来)
SEISAN_STATUS_IDS = {
    "承認依頼中": "denpyoStatus_0",
    "仮払金精算待": "denpyoStatus_-2",
    "支払確定待": "denpyoStatus_-1",
    "支払確定済": "denpyoStatus_-9",
    "差戻し": "denpyoStatus_1",
    "取下げ": "denpyoStatus_2",
    "否認": "denpyoStatus_-99",
    "対象外": "denpyoStatus_-98",
}
SEISAN_STATUS_ORDER = [
    "承認依頼中", "仮払金精算待", "支払確定待", "支払確定済",
    "差戻し", "取下げ", "否認", "対象外",
]


def seisan_login_id(scope):
    return [
        lambda s: s.locator("#loginId"),
        lambda s: s.locator("#d_login_input input[name='loginId']"),
        lambda s: s.get_by_label(re.compile("ログインID")),
    ]


def seisan_password(scope):
    return [
        lambda s: s.locator("#password"),
        lambda s: s.locator("input[type=password]"),
    ]


def seisan_submit(scope):
    return [
        lambda s: s.locator("#submitBtn"),
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*ログイン\s*$")),
        lambda s: s.locator("input[type=submit][value*='ログイン']"),
    ]


def seisan_login_error(scope):
    return [
        lambda s: s.get_by_text(re.compile(r"正しくありません|誤りがあります|ロックされ|失敗")),
    ]


def seisan_password_expired(scope):
    return [
        lambda s: s.get_by_text(re.compile(r"パスワードの有効期限|パスワードを変更してください")),
    ]


def seisan_category_select(scope):
    return [
        lambda s: s.locator("select.szb-select-small-auto").first,
        lambda s: s.locator("#d_master_top select").first,
    ]


def seisan_item_names_checkbox(scope):
    return [
        lambda s: s.locator("#komokuOutputFlag_1"),
        lambda s: s.get_by_label(re.compile(r"項目名を出力する")),
    ]


def seisan_date_inputs(scope):
    return [
        lambda s: s.locator("input.positiveIntTextBox.imeOff.d_widthTxt4"),
        lambda s: s.locator("#d_master_top .szb-date-picker input.positiveIntTextBox"),
    ]


def seisan_status_checkbox(status_id):
    return [lambda s, i=status_id: s.locator("#" + _css_id(i))]


def seisan_clear_all(scope):
    return [lambda s: s.get_by_role("button", name=re.compile(r"^\s*(すべて|全て)解除\s*$"))]


def seisan_file_output(scope):
    return [
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*ファイル出力\s*$")),
        lambda s: s.locator("button.imgButton.output"),
        lambda s: s.locator("input[type=submit][value='ファイル出力'], input[type=button][value='ファイル出力']"),
    ]


def seisan_logout(scope):
    return [lambda s: s.get_by_role("link", name=re.compile(r"ログアウト"))]


def _css_id(value):
    # type: (str) -> str
    # id に含まれる '-' はそのまま、# セレクタで使うためエスケープ不要 (英数と_-のみ)
    return value


# ---------------------------------------------------------------- 楽楽勤怠 (Kintai)
_LOGIN_FORM = "#login-app form[action$='login.authenticate']"
_TEXT_INPUTS = _LOGIN_FORM + " input:not([type=checkbox]):not([type=hidden]):not([type=submit])"

# "2026年09月( 2026年09月01日 - 2026年09月30日 )"
KINTAI_PERIOD_RE = re.compile(
    r"(\d{4})年(\d{1,2})月\s*[(（]\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*[-‐－ー~〜～]\s*"
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[)）]"
)
KINTAI_PERIOD_CONTAINER = "#attendance_management"
KINTAI_DONE_TEXT = re.compile(r"処理完了|100\s*%")
KINTAI_FAILED_TEXT = re.compile(r"エクスポートに失敗|処理失敗|エラーが発生|処理できませんでした")


def kintai_company(scope):
    return [
        lambda s: s.get_by_label(re.compile(r"お客様ID|Customer ID")),
        lambda s: s.locator(_TEXT_INPUTS).nth(0),
    ]


def kintai_login_id(scope):
    return [
        lambda s: s.get_by_label(re.compile(r"ログインID|Login ID")),
        lambda s: s.locator(_TEXT_INPUTS).nth(1),
    ]


def kintai_password(scope):
    return [
        lambda s: s.get_by_label(re.compile(r"パスワード|Password")),
        lambda s: s.locator(_LOGIN_FORM + " input[type=password]"),
    ]


def kintai_remember(scope):
    return [
        lambda s: s.get_by_label(re.compile(r"ログイン情報を保持する|Remember my login")),
        lambda s: s.locator(_LOGIN_FORM + " input[type=checkbox]"),
    ]


def kintai_submit(scope):
    return [
        lambda s: s.locator(_LOGIN_FORM + " button[type=submit]"),
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*(ログイン|Login)\s*$")),
    ]


def kintai_login_error(scope):
    return [
        lambda s: s.get_by_text(re.compile(r"正しくありません|入力してください|ロックされ|incorrect|invalid", re.I)),
        lambda s: s.locator("#login-app [role=alert], #login-app .text-error, #login-app .error"),
    ]


def kintai_password_expired(scope):
    return [lambda s: s.get_by_text(re.compile(r"パスワードの有効期限|password has expired", re.I))]


def kintai_app_header(scope):
    return [lambda s: s.locator("#appHeader")]


def kintai_menu_tab(scope):
    return [
        lambda s: s.locator("#appHeader li.base_header_tab > button:has-text('勤怠管理')"),
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*勤怠管理\s*$")),
    ]


def kintai_menu_item(scope):
    return [
        lambda s: s.locator("li#attendanceManager"),
        lambda s: s.get_by_text(re.compile(r"^\s*出勤簿管理\s*$")),
    ]


def kintai_prev_month(scope):
    # 実DOM: <div class="change_date_control"><div class="change_prev"><img></div>
    #        <div class="display_date">…</div><div class="change_next"><img></div></div>
    # (前月/翌月ともテキスト・title 無しの div。<< < 1 2 > >> は従業員ページ送りで別物)。
    return [
        lambda s: s.locator("#attendance_management .change_date_control .change_prev"),
        lambda s: s.locator(".change_date_control .change_prev"),
        lambda s: s.locator("#attendance_management .change_prev"),
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*(前月|前の月|先月)\s*$")),
        lambda s: s.get_by_title(re.compile(r"^(前月|前の月|先月)$")),
    ]


def kintai_next_month(scope):
    return [
        lambda s: s.locator("#attendance_management .change_date_control .change_next"),
        lambda s: s.locator(".change_date_control .change_next"),
        lambda s: s.locator("#attendance_management .change_next"),
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*(翌月|次月)\s*$")),
        lambda s: s.get_by_title(re.compile(r"^(翌月|次月)$")),
    ]


def kintai_export_icon(scope):
    return [
        lambda s: s.get_by_title("勤怠情報のExcelエクスポート"),
        lambda s: s.locator("img[title='勤怠情報のExcelエクスポート']"),
        lambda s: s.locator("#scroll_child .footer_right img[src$='excel_download.svg']"),
    ]


def kintai_export_dialog(scope):
    return [lambda s: s.locator(".dialog_window.excel_download_dialog")]


def kintai_daily_radio(scope):
    # 実DOM: <input type=radio name=export_type value="2"> (2=日別詳細)。input は非表示。
    return [
        lambda s: s.locator('input[type=radio][value="2"]'),
        lambda s: s.locator("label:has-text('日別詳細') input[type=radio]"),
        lambda s: s.get_by_label(re.compile(r"日別詳細")),
    ]


def kintai_daily_label(scope):
    # クリック可能な可視ラベル (input が非表示のため)。
    return [
        lambda s: s.locator("label.radio_button_label:has-text('日別詳細')"),
        lambda s: s.locator("label:has-text('日別詳細')"),
    ]


def kintai_export_button(scope):
    return [
        lambda s: s.locator("footer button.button_positive"),
        lambda s: s.locator("button.label_button.button_positive:has-text('エクスポート')"),
        lambda s: s.get_by_role("button", name=re.compile(r"^\s*エクスポート\s*$")),
    ]


def kintai_progress_modal(scope):
    return [lambda s: s.locator(".dialog_window.file_export_modal")]


def kintai_download_button(scope):
    return [
        lambda s: s.locator("footer a.file_link button"),
        lambda s: s.locator("footer button:has-text('ダウンロード')"),
    ]


def kintai_close_button(scope):
    return [lambda s: s.locator("footer button:has-text('閉じる')")]


def kintai_logout(scope):
    return [lambda s: s.get_by_role("link", name=re.compile(r"ログアウト|Logout"))]
