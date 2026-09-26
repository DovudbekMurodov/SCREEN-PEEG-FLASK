from __future__ import annotations

import datetime as dt

import pytest

from rakuraku.params import (
    ParamError,
    default_kintai_months,
    default_seisan_range,
    parse_kintai,
    parse_seisan,
)

TODAY = dt.date(2026, 9, 25)


def test_seisan_defaults():
    p = parse_seisan({"login_id": "a", "password": "b"}, today=TODAY)
    assert (p.applied_from, p.applied_to) == (dt.date(2026, 8, 25), TODAY)
    assert p.scope == "自部門"
    assert p.statuses == ["承認依頼中"]
    assert default_seisan_range(TODAY) == (dt.date(2026, 8, 25), TODAY)


def test_seisan_statuses_and_scope():
    p = parse_seisan(
        {"login_id": "a", "password": "b", "scope": "全部門",
         "statuses": ["承認依頼中", "差戻し", "でたらめ"]},
        today=TODAY,
    )
    assert p.scope == "全部門"
    assert p.statuses == ["承認依頼中", "差戻し"]


@pytest.mark.parametrize("form,msg", [
    ({}, "ログインID"),
    ({"login_id": "a"}, "パスワード"),
    ({"login_id": "a", "password": "b", "scope": "x"}, "部門"),
    ({"login_id": "a", "password": "b", "applied_from": "2026-09-20", "applied_to": "2026-09-10"}, "開始日"),
    ({"login_id": "a", "password": "b", "applied_to": "2026-12-31"}, "未来"),
    ({"login_id": "a", "password": "b", "applied_from": "2026-01-01", "applied_to": "2026-09-25"}, "93"),
    ({"login_id": "a", "password": "b", "applied_from": "bad"}, "形式"),
])
def test_seisan_validation(form, msg):
    with pytest.raises(ParamError) as e:
        parse_seisan(form, today=TODAY)
    assert msg in str(e.value)


def test_kintai_defaults_and_months():
    p = parse_kintai({"company_code": "PEEG", "login_id": "a", "password": "b"}, today=TODAY)
    assert p.company_code == "PEEG"
    assert p.months == ["2026-09", "2026-08"]
    assert default_kintai_months(TODAY) == ["2026-09", "2026-08"]


def test_kintai_month_selection_dedup_sorted_capped():
    p = parse_kintai(
        {"company_code": "P", "login_id": "a", "password": "b",
         "months": ["2026-07", "2026-09", "2026-09", "2026-05"]},
        today=TODAY, max_months=2,
    )
    assert p.months == ["2026-09", "2026-07"]


def test_kintai_requires_company():
    with pytest.raises(ParamError):
        parse_kintai({"login_id": "a", "password": "b"}, today=TODAY)
