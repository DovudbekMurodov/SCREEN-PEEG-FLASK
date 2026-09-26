from __future__ import annotations

import re
from pathlib import Path

import pytest

from i18n import CATALOG, translate
from rakuraku import errors as errmod
from rakuraku.errors import RakuError

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
JP = re.compile(r"[぀-ヿ一-鿿]")


def test_default_is_japanese():
    assert translate("楽楽精算") == "楽楽精算"
    assert translate("CSVを取得する", "ja") == "CSVを取得する"
    assert translate("未知テキスト", "en") == "未知テキスト"


def test_exact_and_pattern():
    assert translate("CSVを取得する", "en") == "Download CSV"
    assert translate("CSVを取得する", "uz") == "CSV yuklab olish"
    assert translate("申請日の範囲は93日以内にしてください。", "en") == "The date range must be at most 93 days."
    assert "93" in translate("申請日の範囲は93日以内にしてください。", "uz")


def test_service_placeholder_kept():
    tmpl = errmod.LoginFailed().template
    en = translate(tmpl, "en")
    assert "{service}" in en
    assert en.format(service="楽楽精算").startswith("Could not log in to 楽楽精算")


def _template_strings():
    found = set()
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'_\(\s*"([^"]+)"', text))
        found |= set(re.findall(r"_\(\s*'([^']+)'", text))
    return found


def _python_messages():
    from rakuraku import params

    msgs = set()
    for cls in vars(errmod).values():
        if isinstance(cls, type) and issubclass(cls, RakuError):
            msgs.add(cls.user_message)  # unformatted template ({service})
    # param error strings
    import datetime as dt

    bad = [
        {"applied_from": "2026-09-20", "applied_to": "2026-09-10"},
        {"applied_from": "2026-01-01", "applied_to": "2026-09-25"},
        {"applied_from": "bad", "applied_to": ""},
    ]
    for form in bad:
        form = dict(form, login_id="x", password="y")
        try:
            params.parse_seisan(form, today=dt.date(2026, 9, 25))
        except params.ParamError as exc:
            msgs.add(str(exc))
    for key in ("ログインIDを入力してください。", "パスワードを入力してください。", "お客様IDを入力してください。"):
        msgs.add(key)
    return msgs


@pytest.mark.parametrize("lang", ["en", "uz"])
def test_templates_translated(lang):
    missing = sorted(s for s in _template_strings() if JP.search(s) and s not in CATALOG)
    assert not missing, "add to i18n_catalog.py: %s" % missing


@pytest.mark.parametrize("lang", ["en", "uz"])
def test_python_messages_translated(lang):
    bad = sorted(m for m in _python_messages() if JP.search(m) and translate(m, lang) == m)
    assert not bad, "no %s translation for: %s" % (lang, bad)


def test_placeholders_consistent():
    ph = re.compile(r"\{(\w+)\}")
    for key, (en, uz) in CATALOG.items():
        want = set(ph.findall(key))
        assert set(ph.findall(en)) == want, key
        assert set(ph.findall(uz)) == want, key
