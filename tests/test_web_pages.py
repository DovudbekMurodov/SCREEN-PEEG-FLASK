from __future__ import annotations


def test_index_still_works_with_navbar(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "チェックシートを作成する" in body  # existing feature intact
    assert 'href="/seisan"' in body and 'href="/kintai"' in body  # navbar added
    assert "楽楽精算" in body and "楽楽勤怠" in body


def test_seisan_page(client):
    r = client.get("/seisan")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "ログインID" in body and "CSVを取得する" in body
    assert 'name="statuses"' in body and "denpyoStatus" not in body  # UI uses labels, not raw ids


def test_kintai_page(client):
    r = client.get("/kintai")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "お客様ID" in body and "出勤簿を取得する" in body
    assert 'value="PEEG"' in body


def test_language_switch_cookie_and_render(client):
    r = client.get("/lang/en?next=/seisan")
    assert r.status_code == 303
    assert r.headers["Location"] == "/seisan"
    r2 = client.get("/seisan")
    body = r2.get_data(as_text=True)
    assert "Download CSV" in body
    assert '<html lang="en">' in body

    client.get("/lang/uz")
    assert "CSV yuklab olish" in client.get("/seisan").get_data(as_text=True)

    client.get("/lang/ja")
    assert "CSVを取得する" in client.get("/seisan").get_data(as_text=True)


def test_invalid_language_ignored(client):
    r = client.get("/lang/zz?next=/")
    assert r.status_code == 303
    assert "rr_lang=zz" not in r.headers.get("Set-Cookie", "")


def test_names_never_translated(client):
    client.get("/lang/uz")
    body = client.get("/").get_data(as_text=True)
    assert "楽楽精算" in body  # product name stays Japanese even in uz
