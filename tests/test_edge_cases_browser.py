"""楽楽精算/楽楽勤怠の境界ケースを擬似サイト+実ブラウザで確認する (利用者が遭遇しうる画面の出方)。"""
from __future__ import annotations

import datetime as dt
import time

import pytest

from rakuraku.browser import browser_session
from rakuraku.errors import (
    AccountLocked,
    AttendanceUnavailable,
    AdditionalAuthRequired,
    ExportFailed,
    LoginFailed,
    NoDataFound,
    PasswordExpired,
    SiteUnavailable,
)
from rakuraku.kintai import KintaiClient
from rakuraku.params import KintaiParams, SeisanParams
from rakuraku.seisan import SeisanClient
from tests.conftest import chromium_available

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not chromium_available(), reason="playwright chromium not installed"),
]


def _params(**over):
    base = dict(
        login_id="AZ999999", password="seisan-pass", scope="自部門", category="出張精算(MEBA)",
        applied_from=dt.date(2026, 8, 25), applied_to=dt.date(2026, 9, 25), statuses=["承認依頼中"],
    )
    base.update(over)
    return SeisanParams(**base)


@pytest.fixture
def ctx():
    with browser_session(headless=True) as context:
        yield context


def _seisan(ctx, raku, **kw):
    kw.setdefault("nav_timeout_ms", 4000)
    kw.setdefault("download_timeout_ms", 30000)
    return SeisanClient(ctx, raku.seisan_base(), **kw)


def _kintai(ctx, raku):
    return KintaiClient(ctx, raku.base + "/app/login", raku.base + "/app/attendancemanagement",
                        nav_timeout_ms=4000, export_timeout_s=30)


# ------------------------------------------------------------------ 楽楽精算: ログイン
def test_seisan_password_expired(ctx, raku):
    raku.state["seisan_login_mode"] = "expired"
    with pytest.raises(PasswordExpired):
        _seisan(ctx, raku).download(_params())


def test_seisan_two_factor_page_stops(ctx, raku):
    raku.state["seisan_login_mode"] = "twofactor"
    with pytest.raises(AdditionalAuthRequired):
        _seisan(ctx, raku).download(_params())


def test_seisan_unknown_login_message_is_still_shown(ctx, raku):
    # 想定外の文言でログイン画面のまま → LoginFailed だが、楽楽の文言は利用者に見せる
    raku.state["seisan_login_mode"] = "unknown"
    with pytest.raises(LoginFailed) as ei:
        _seisan(ctx, raku).download(_params())
    assert ei.value.site_message == "認証できませんでした。"


def test_seisan_locked_client_level(ctx, raku):
    raku.state["seisan_locked"] = True
    with pytest.raises(AccountLocked) as ei:
        _seisan(ctx, raku).download(_params())
    assert "ロック" in ei.value.site_message


def test_seisan_site_down(ctx):
    with pytest.raises(SiteUnavailable):
        SeisanClient(ctx, "http://127.0.0.1:9/tenant/", nav_timeout_ms=4000).download(_params())


# ------------------------------------------------------------------ 楽楽精算: ファイル出力
@pytest.mark.parametrize("mode", ["alert_then_download", "confirm", "popup"])
def test_seisan_download_variants_succeed(ctx, raku, mode):
    raku.state["seisan_export_mode"] = mode
    name, data = _seisan(ctx, raku).download(_params())
    assert name.endswith(".csv") and "伝票No" in data.decode("cp932")


def test_seisan_slow_export_with_progress_text_is_not_mistaken_for_error(ctx, raku):
    raku.state["seisan_export_mode"] = "slow"
    started = time.monotonic()
    name, data = _seisan(ctx, raku).download(_params())
    assert "伝票No" in data.decode("cp932") and time.monotonic() - started >= 7


@pytest.mark.parametrize("mode", ["alert", "page"])
def test_seisan_no_data_client_level(ctx, raku, mode):
    raku.state["seisan_export_mode"] = mode
    with pytest.raises(NoDataFound) as ei:
        _seisan(ctx, raku).download(_params())
    assert ei.value.site_message == "出力対象のデータが存在しません。"


def test_seisan_validation_message_is_export_failed_with_text(ctx, raku):
    raku.state["seisan_export_mode"] = "validation"
    with pytest.raises(ExportFailed) as ei:
        _seisan(ctx, raku).download(_params())
    assert ei.value.site_message == "申請日の日付が正しくありません。"


@pytest.mark.parametrize("mode", ["badcsv", "emptycsv"])
def test_seisan_unexpected_file_is_rejected(ctx, raku, mode):
    raku.state["seisan_export_mode"] = mode
    with pytest.raises(ExportFailed):
        _seisan(ctx, raku).download(_params())


def test_seisan_all_statuses_and_other_category(ctx, raku):
    statuses = ["承認依頼中", "仮払金精算待", "支払確定待", "支払確定済", "差戻し", "取下げ", "否認", "対象外"]
    name, data = _seisan(ctx, raku).download(_params(statuses=statuses, category="経費精算"))
    assert "伝票No" in data.decode("cp932")


# ------------------------------------------------------------------ 楽楽勤怠: ログイン
def test_kintai_locked(ctx, raku):
    raku.state["kintai_login_mode"] = "locked"
    with pytest.raises(AccountLocked) as ei:
        _kintai(ctx, raku).download(KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09"]))
    assert ei.value.site_message == "アカウントがロックされています"


def test_kintai_password_expired(ctx, raku):
    raku.state["kintai_login_mode"] = "expired"
    with pytest.raises(PasswordExpired):
        _kintai(ctx, raku).download(KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09"]))


def test_kintai_wrong_company_shows_site_text(ctx, raku):
    with pytest.raises(LoginFailed) as ei:
        _kintai(ctx, raku).download(KintaiParams("XXXX", "AZ999999", "kintai-pass", ["2026-09"]))
    assert ei.value.site_message == "正しくありません"


def test_kintai_three_months(ctx, raku):
    files = _kintai(ctx, raku).download(KintaiParams("PEEG", "AZ999999", "kintai-pass",
                                                     ["2026-09", "2026-08", "2026-07"]))
    assert [n[:7] for n, _ in files] == ["2026-09", "2026-08", "2026-07"]
    assert raku.state["forbidden"] == []


def test_kintai_account_without_attendance_management(ctx, raku):
    # 実利用で起きた事象 (岡部さん): ログインはできるが、一般社員のため「出勤簿管理」が無い。
    raku.state["kintai_no_permission"] = True
    with pytest.raises(AttendanceUnavailable) as ei:
        _kintai(ctx, raku).download(KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09"]))
    log = ei.value.log
    assert "URL: /app/home" in log and "打刻" in log and "出勤簿" in log
