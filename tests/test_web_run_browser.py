"""/seisan/run と /kintai/run を実ブラウザ+擬似サイトで通す統合テスト。"""
from __future__ import annotations

import pytest

import rakuraku_web
from tests.conftest import chromium_available

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not chromium_available(), reason="playwright chromium not installed"),
]


@pytest.fixture
def wired(raku, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "SEISAN_BASE_URL", raku.seisan_base())
    monkeypatch.setattr(rakuraku_web, "KINTAI_LOGIN_URL", raku.base + "/app/login")
    monkeypatch.setattr(rakuraku_web, "KINTAI_ATTENDANCE_URL", raku.base + "/app/attendancemanagement")
    return raku


def test_seisan_run_end_to_end(client, wired):
    r = client.post("/seisan/run", data={
        "login_id": "AZ999999", "password": "seisan-pass", "scope": "自部門",
        "applied_from": "2026-08-25", "applied_to": "2026-09-25", "statuses": "承認依頼中",
    })
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    assert r.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in r.headers["Content-Disposition"]
    assert "伝票No" in r.get_data().decode("cp932")
    assert wired.state["seisan_logins"] == 1


def test_seisan_run_wrong_password_localized(client, wired):
    client.get("/lang/en")
    r = client.post("/seisan/run", data={"login_id": "AZ999999", "password": "nope"})
    assert r.status_code == 502
    assert "Could not log in to 楽楽精算" in r.get_data(as_text=True)


def test_seisan_stream_events(client, wired):
    r = client.post("/seisan/stream", data={
        "login_id": "AZ999999", "password": "seisan-pass", "statuses": "承認依頼中",
    })
    body = r.get_data(as_text=True)
    assert r.headers["Content-Type"].startswith("text/event-stream")
    assert "event: steps" in body
    assert '"key": "login", "status": "done"' in body
    assert "event: file" in body
    assert "event: error" not in body


def test_seisan_stream_error_event_on_bad_login(client, wired):
    body = client.post("/seisan/stream", data={"login_id": "AZ999999", "password": "nope"}).get_data(as_text=True)
    assert "event: error" in body
    assert "event: file" not in body


def test_kintai_stream_events(client, wired):
    body = client.post("/kintai/stream", data={
        "company_code": "PEEG", "login_id": "AZ999999", "password": "kintai-pass", "months": "2026-09",
    }).get_data(as_text=True)
    assert "event: steps" in body
    assert "event: file" in body
    assert "event: error" not in body


def test_kintai_run_end_to_end(client, wired):
    r = client.post("/kintai/run", data={
        "company_code": "PEEG", "login_id": "AZ999999", "password": "kintai-pass", "months": "2026-09",
    })
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    assert "spreadsheetml" in r.headers["Content-Type"]
    assert r.get_data()[:4] == b"PK\x03\x04"
    assert wired.state["forbidden"] == []


def test_kintai_stream_skips_empty_month_and_still_delivers_file(client, wired):
    # 2026-08 が0件でも致命にせず: ⚠ skipped イベント (表示言語で翻訳) + 2026-09 のファイルを配信。
    wired.state["empty_months"].append("2026-08")
    client.get("/lang/uz")
    body = client.post("/kintai/stream", data={
        "company_code": "PEEG", "login_id": "AZ999999", "password": "kintai-pass",
        "months": ["2026-09", "2026-08"],
    }).get_data(as_text=True)
    assert '"key": "export:2026-09", "status": "done"' in body
    assert '"key": "export:2026-08", "status": "skipped"' in body
    assert "2026-08 uchun davomat" in body  # detail は uz に翻訳される
    assert "event: file" in body
    assert "event: error" not in body


def test_kintai_stream_all_months_empty_is_error(client, wired):
    wired.state["empty_months"].append("2026-09")
    body = client.post("/kintai/stream", data={
        "company_code": "PEEG", "login_id": "AZ999999", "password": "kintai-pass", "months": "2026-09",
    }).get_data(as_text=True)
    assert "event: error" in body
    assert "選択した月の出勤簿データがありませんでした" in body
    assert "event: file" not in body


# ---------------------------------------------------------------- ワンクリック実行 (実ブラウザ + 擬似サイト + 実エンジン)
ONECLICK_FORM = {"s_login_id": "AZ999999", "s_password": "seisan-pass", "k_company_code": "PEEG",
                 "k_login_id": "AZ999999", "k_password": "kintai-pass"}


def test_oneclick_stream_end_to_end(client, wired, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "today_jst", lambda: __import__("datetime").date(2026, 9, 28))
    from tests.test_oneclick import _attendance_count, sse_events

    events = sse_events(client.post("/oneclick/stream", data=ONECLICK_FORM).get_data(as_text=True))
    assert "error" not in [e for e, _ in events], events
    result = [d for e, d in events if e == "result"][0]
    names = [f["filename"] for f in result["files"]]
    assert any(n.endswith(".xlsx") for n in names) and any(n.endswith("_ja.html") for n in names)
    assert _attendance_count(result["log"]) == 61  # 擬似サイトの 2026-09 + 2026-08 が読めている
    assert wired.state["seisan_logins"] == 1 and wired.state["kintai_logins"] == 1
    assert wired.state["forbidden"] == []


def test_oneclick_stream_skips_empty_month(client, wired, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "today_jst", lambda: __import__("datetime").date(2026, 9, 28))
    from tests.test_oneclick import sse_events

    wired.state["empty_months"].append("2026-08")
    events = sse_events(client.post("/oneclick/stream", data=ONECLICK_FORM).get_data(as_text=True))
    result = [d for e, d in events if e == "result"][0]
    assert result["skipped"] == ["2026-08"]
    assert {"key": "k:export:2026-08", "status": "skipped"}.items() <= [
        d for e, d in events if e == "step" and d["key"] == "k:export:2026-08"][-1].items()


def test_oneclick_stream_all_months_empty_is_error(client, wired, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "today_jst", lambda: __import__("datetime").date(2026, 9, 28))
    from tests.test_oneclick import sse_events

    wired.state["empty_months"].extend(["2026-09", "2026-08"])
    events = sse_events(client.post("/oneclick/stream", data=ONECLICK_FORM).get_data(as_text=True))
    assert events[-1][0] == "error" and "result" not in [e for e, _ in events]
