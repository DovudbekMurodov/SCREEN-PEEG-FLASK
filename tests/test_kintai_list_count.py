"""getMonthlyList 応答の行数推定 (_list_count) — 実応答の形に対して 0件/件数/不明を正しく返すこと。"""
from __future__ import annotations

from rakuraku.kintai import _list_count

REAL_EMPTY = {"data": {"list_data": [], "pager": {"total_count": 0, "limit": 20},
                       "period_list": [{"ym": "2026-09"}, {"ym": "2026-08"}], "index": {}, "filter": {}}}
REAL_ROWS = {"data": {"list_data": [{"employee_id": "AH000135"}] * 20,
                      "pager": {"total_count": 37, "limit": 20}, "period_list": [{"ym": "2026-09"}]}}


def test_real_shape_empty_month_is_zero():
    assert _list_count(REAL_EMPTY) == 0


def test_real_shape_rows_counts_list_data():
    assert _list_count(REAL_ROWS) == 20


def test_pager_total_count_used_when_no_row_list():
    assert _list_count({"data": {"pager": {"total_count": 37}, "period_list": [{}, {}]}}) == 37


def test_unrelated_lists_never_counted():
    # period_list / columns などの長さを行数と誤認しない → 不明 (None)
    assert _list_count({"data": {"period_list": [{"ym": "a"}, {"ym": "b"}], "columns": [{"k": 1}]}}) is None


def test_flat_and_unknown_shapes():
    assert _list_count({"total_count": 0}) == 0
    assert _list_count({"list": [{"a": 1}]}) == 1
    assert _list_count({"result": {"rows": []}}) == 0
    assert _list_count({"ok": True}) is None
    assert _list_count([]) is None
    assert _list_count("nope") is None
