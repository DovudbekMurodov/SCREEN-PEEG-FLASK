from __future__ import annotations

import contextlib
import io
import zipfile

import pytest

import rakuraku_web


@contextlib.contextmanager
def _fake_session(**kwargs):
    yield object()


@pytest.fixture
def no_browser(monkeypatch):
    monkeypatch.setattr(rakuraku_web, "playwright_available", lambda: True)
    monkeypatch.setattr(rakuraku_web, "browser_session", _fake_session)


class _FakeSeisan:
    def __init__(self, *a, **k):
        pass

    def download(self, params):
        assert params.statuses  # params were parsed
        return "出張精算_20260101_000000.csv", "伝票No\n10231\n".encode("cp932")

    def logout(self):
        pass


class _FakeKintai:
    files = None

    def __init__(self, *a, **k):
        pass

    def download(self, params, progress=None, on_step=None):
        return list(_FakeKintai.files)

    def logout(self):
        pass


def test_seisan_run_returns_csv(client, no_browser, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "SeisanClient", _FakeSeisan)
    r = client.post("/seisan/run", data={"login_id": "AZ999999", "password": "x"})
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in r.headers["Content-Disposition"]
    assert "伝票No" in r.get_data().decode("cp932")


def test_seisan_run_validation_error(client, no_browser, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "SeisanClient", _FakeSeisan)
    r = client.post("/seisan/run", data={"login_id": "", "password": "x"})
    assert r.status_code == 400
    assert "ログインID" in r.get_data(as_text=True)


def test_seisan_run_paid_plan_message(client, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "playwright_available", lambda: False)
    r = client.post("/seisan/run", data={"login_id": "a", "password": "b"})
    assert r.status_code == 503
    assert "有料プラン" in r.get_data(as_text=True)


def test_seisan_run_error_localized_in_english(client, no_browser, monkeypatch):
    class Boom(_FakeSeisan):
        def download(self, params):
            from rakuraku.errors import LoginFailed

            raise LoginFailed(service="seisan")

    monkeypatch.setattr(rakuraku_web, "SeisanClient", Boom)
    client.get("/lang/en")
    r = client.post("/seisan/run", data={"login_id": "a", "password": "b"})
    assert r.status_code == 502
    assert "Could not log in to 楽楽精算" in r.get_data(as_text=True)


def test_kintai_run_single_file_xlsx(client, no_browser, monkeypatch):
    _FakeKintai.files = [("2026-09_出勤簿.xlsx", b"PK\x03\x04single")]
    monkeypatch.setattr(rakuraku_web, "KintaiClient", _FakeKintai)
    r = client.post("/kintai/run", data={"company_code": "PEEG", "login_id": "a", "password": "b", "months": "2026-09"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["Content-Type"]
    assert r.get_data() == b"PK\x03\x04single"


def test_kintai_run_multiple_files_zip(client, no_browser, monkeypatch):
    _FakeKintai.files = [("2026-09_a.xlsx", b"PK\x03\x04aaa"), ("2026-08_b.xlsx", b"PK\x03\x04bbb")]
    monkeypatch.setattr(rakuraku_web, "KintaiClient", _FakeKintai)
    r = client.post(
        "/kintai/run",
        data={"company_code": "PEEG", "login_id": "a", "password": "b", "months": ["2026-09", "2026-08"]},
    )
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    assert sorted(zf.namelist()) == ["2026-08_b.xlsx", "2026-09_a.xlsx"]


def test_password_not_in_response(client, no_browser, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "SeisanClient", _FakeSeisan)
    r = client.post("/seisan/run", data={"login_id": "AZ999999", "password": "S3cr3t-PW"})
    assert b"S3cr3t-PW" not in r.get_data()
