from __future__ import annotations

import datetime as dt

import pytest

from rakuraku.browser import browser_session
from rakuraku.errors import ForbiddenActionBlocked, LoginFailed, NoDataFound
from rakuraku.kintai import KintaiClient
from rakuraku.params import KintaiParams, SeisanParams
from rakuraku.seisan import SeisanClient
from tests.conftest import chromium_available

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not chromium_available(), reason="playwright chromium not installed"),
]


def _seisan_params(**over):
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


def test_seisan_download(ctx, raku):
    client = SeisanClient(ctx, raku.seisan_base())
    name, data = client.download(_seisan_params())
    assert name.startswith("出張精算_") and name.endswith(".csv")
    assert "伝票No" in data.decode("cp932")
    assert raku.state["seisan_logins"] == 1


def test_seisan_wrong_password(ctx, raku):
    client = SeisanClient(ctx, raku.seisan_base())
    with pytest.raises(LoginFailed):
        client.download(_seisan_params(password="wrong"))


def test_kintai_single_month(ctx, raku):
    client = KintaiClient(ctx, raku.base + "/app/login", raku.base + "/app/attendancemanagement", export_timeout_s=30)
    files = client.download(KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09"]))
    assert len(files) == 1
    name, data = files[0]
    assert "日別詳細" in name and data[:4] == b"PK\x03\x04"
    assert raku.state["forbidden"] == []  # never clicked 承認/差戻し
    assert raku.state["kintai_remember"] == [False]  # "remember" unchecked


def test_kintai_two_months_prev_nav(ctx, raku):
    client = KintaiClient(ctx, raku.base + "/app/login", raku.base + "/app/attendancemanagement", export_timeout_s=30)
    files = client.download(KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09", "2026-08"]))
    assert [n.split("_")[0] for n in (f[0] for f in files)] == ["2026-09", "2026-08"]
    assert raku.state["forbidden"] == []
    # 2か月目(2026-08)は矢印クリックで月リストAPIが呼ばれて読み込まれること。
    assert raku.state.get("kintai_list_calls", 0) >= 1


def test_kintai_wrong_password(ctx, raku):
    client = KintaiClient(ctx, raku.base + "/app/login", raku.base + "/app/attendancemanagement")
    with pytest.raises(LoginFailed):
        client.download(KintaiParams("PEEG", "AZ999999", "wrong", ["2026-09"]))


def test_kintai_empty_month_is_skipped_not_fatal(ctx, raku):
    # 実サイト: 行が0件の月はグリッドもExcelアイコンも描画されない。その月は
    # ⚠ スキップして、データのある月だけ返す (致命エラーにしない)。
    raku.state["empty_months"].append("2026-08")
    seen = []
    client = KintaiClient(ctx, raku.base + "/app/login", raku.base + "/app/attendancemanagement", export_timeout_s=30)
    files = client.download(
        KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09", "2026-08"]),
        on_step=lambda key, status, detail=None: seen.append((key, status, detail)),
    )
    assert [f[0].split("_")[0] for f in files] == ["2026-09"]
    assert client.skipped == ["2026-08"]
    skipped = [s for s in seen if s[0] == "export:2026-08" and s[1] == "skipped"]
    assert skipped and "2026-08" in skipped[0][2] and "スキップ" in skipped[0][2]
    assert ("export:2026-09", "done", None) in seen
    assert raku.state["forbidden"] == []


def test_kintai_all_months_empty_raises_no_data(ctx, raku):
    raku.state["empty_months"].append("2026-09")
    client = KintaiClient(ctx, raku.base + "/app/login", raku.base + "/app/attendancemanagement", export_timeout_s=30)
    with pytest.raises(NoDataFound) as info:
        client.download(KintaiParams("PEEG", "AZ999999", "kintai-pass", ["2026-09"]))
    assert "選択した月" in info.value.user_message


def test_forbidden_click_guard(ctx):
    from rakuraku.locators import safe_click

    page = ctx.new_page()
    page.set_content("<button>差し戻しへ</button><button>エクスポート</button>")
    with pytest.raises(ForbiddenActionBlocked):
        safe_click(page.get_by_role("button", name="差し戻しへ"))
    safe_click(page.get_by_role("button", name="エクスポート"))  # allowed
