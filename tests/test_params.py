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


# ---------------------------------------------------------------- 入力の揺れ (実利用で起きうるもの)
def test_fullwidth_ids_are_normalized_but_password_is_kept_verbatim():
    from rakuraku.params import parse_kintai, parse_seisan

    k = parse_kintai({"company_code": "ＰＥＥＧ ", "login_id": " ＡＺ９９９９９９", "password": " pa ss "},
                     today=dt.date(2026, 9, 30))
    assert (k.company_code, k.login_id, k.password) == ("PEEG", "AZ999999", " pa ss ")
    s = parse_seisan({"login_id": "ＡＺ１", "password": "pw "}, today=dt.date(2026, 9, 30))
    assert (s.login_id, s.password) == ("AZ1", "pw ")


def test_blank_password_or_category_handled():
    from rakuraku.params import ParamError, parse_seisan

    with pytest.raises(ParamError):
        parse_seisan({"login_id": "a", "password": "   "}, today=dt.date(2026, 9, 30))
    s = parse_seisan({"login_id": "a", "password": "p", "category": "   "}, today=dt.date(2026, 9, 30))
    assert s.category == "出張精算(MEBA)"


def test_range_limit_counts_both_ends():
    from rakuraku.params import MAX_RANGE_DAYS, ParamError, parse_seisan

    to = dt.date(2026, 9, 30)
    ok_from = to - dt.timedelta(days=MAX_RANGE_DAYS - 1)  # ちょうど93日間
    parse_seisan({"login_id": "a", "password": "p", "applied_from": ok_from.isoformat(),
                  "applied_to": to.isoformat()}, today=to)
    with pytest.raises(ParamError):
        parse_seisan({"login_id": "a", "password": "p", "applied_from": (ok_from - dt.timedelta(days=1)).isoformat(),
                      "applied_to": to.isoformat()}, today=to)


def test_future_kintai_months_ignored():
    from rakuraku.params import parse_kintai

    k = parse_kintai({"company_code": "P", "login_id": "a", "password": "p", "months": ["2026-10", "2026-09"]},
                     today=dt.date(2026, 9, 30))
    assert k.months == ["2026-09"]


def test_duplicate_keys_do_not_crash():
    from rakuraku.params import parse_seisan

    s = parse_seisan({"login_id": ["a", "b"], "password": ["p"]}, today=dt.date(2026, 9, 30))
    assert s.login_id == "a"
