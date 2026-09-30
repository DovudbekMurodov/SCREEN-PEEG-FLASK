"""楽楽側の表示文言: 該当なし判定の正規表現と、エラー文への添付 (多言語)。"""
from __future__ import annotations

import pytest

from rakuraku.errors import AccountLocked, LoginFailed
from rakuraku.selectors import SEISAN_NO_DATA
from rakuraku_web import _localize_error


@pytest.mark.parametrize("text", [
    "出力対象のデータが存在しません。", "該当する伝票がありません", "対象データはありません",
    "出力するデータがありません", "データが存在しません", "検索結果：0件",
])
def test_no_data_wording_variants(text):
    assert SEISAN_NO_DATA.search(text)


@pytest.mark.parametrize("text", ["表示件数 10件", "項目名を出力する", "伝票データ出力【自部門】", "申請日を入力してください"])
def test_no_data_ignores_normal_screen_text(text):
    assert not SEISAN_NO_DATA.search(text)


def test_site_message_appended_per_language():
    exc = LoginFailed("x", service="seisan", site_message="パスワードが違います")
    assert _localize_error(exc, "ja").endswith("（楽楽精算の表示：「パスワードが違います」）")
    assert "楽楽精算 showed: “パスワードが違います”" in _localize_error(exc, "en")
    assert "楽楽精算 xabari: “パスワードが違います”" in _localize_error(exc, "uz")


def test_no_site_message_keeps_plain_text():
    assert _localize_error(LoginFailed("x", service="kintai"), "ja") == \
        "楽楽勤怠にログインできませんでした。IDとパスワードをご確認ください。"


def test_account_locked_translated():
    assert "account is locked" in _localize_error(AccountLocked("x", service="seisan"), "en")
