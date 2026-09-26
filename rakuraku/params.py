"""フォーム入力のパラメータ定義とバリデーション。"""
from __future__ import annotations

import calendar
import datetime as _dt
from dataclasses import dataclass, field

try:
    from zoneinfo import ZoneInfo

    _JST = ZoneInfo("Asia/Tokyo")
except Exception:  # noqa: BLE001  (tzdata 不在時は固定 +9)
    _JST = _dt.timezone(_dt.timedelta(hours=9))

MAX_RANGE_DAYS = 93
SCOPES = ("自部門", "全部門")
STATUS_ALL = (
    "承認依頼中", "仮払金精算待", "支払確定待", "支払確定済",
    "差戻し", "取下げ", "否認", "対象外",
)


class ParamError(ValueError):
    """利用者に見せる (翻訳される) 検証エラー。"""


def today_jst():
    # type: () -> _dt.date
    return _dt.datetime.now(_JST).date()


def _shift_month(d, months):
    # type: (_dt.date, int) -> _dt.date
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return _dt.date(year, month, min(d.day, last))


def year_month(d):
    # type: (_dt.date) -> str
    return "%04d-%02d" % (d.year, d.month)


def default_seisan_range(today=None):
    # type: (_dt.date | None) -> tuple[_dt.date, _dt.date]
    today = today or today_jst()
    return _shift_month(today, -1), today


def default_kintai_months(today=None):
    # type: (_dt.date | None) -> list[str]
    today = today or today_jst()
    return [year_month(today), year_month(_shift_month(today, -1))]


@dataclass
class SeisanParams:
    login_id: str
    password: str
    scope: str
    category: str
    applied_from: _dt.date
    applied_to: _dt.date
    statuses: list  # type: list[str]


@dataclass
class KintaiParams:
    company_code: str
    login_id: str
    password: str
    months: list = field(default_factory=list)  # type: list[str]


def _req(form, key, message):
    # type: (dict, str, str) -> str
    value = (form.get(key) or "").strip()
    if not value:
        raise ParamError(message)
    return value


def parse_seisan(form, today=None):
    # type: (dict, _dt.date | None) -> SeisanParams
    today = today or today_jst()
    login_id = _req(form, "login_id", "ログインIDを入力してください。")
    password = _req(form, "password", "パスワードを入力してください。")
    scope = (form.get("scope") or "自部門").strip()
    if scope not in SCOPES:
        raise ParamError("部門の指定が不正です。")
    category = (form.get("category") or "出張精算(MEBA)").strip()

    default_from, default_to = default_seisan_range(today)
    applied_from = _parse_date(form.get("applied_from"), default_from)
    applied_to = _parse_date(form.get("applied_to"), default_to)
    if applied_from > applied_to:
        raise ParamError("申請日の開始日は終了日以前にしてください。")
    if applied_to > today:
        raise ParamError("申請日の終了日に未来の日付は指定できません。")
    if (applied_to - applied_from).days > MAX_RANGE_DAYS:
        raise ParamError("申請日の範囲は{n}日以内にしてください。".format(n=MAX_RANGE_DAYS))

    statuses = [s for s in _status_list(form) if s in STATUS_ALL]
    if not statuses:
        statuses = ["承認依頼中"]
    return SeisanParams(login_id, password, scope, category, applied_from, applied_to, statuses)


def parse_kintai(form, today=None, max_months=3):
    # type: (dict, _dt.date | None, int) -> KintaiParams
    today = today or today_jst()
    company = _req(form, "company_code", "お客様IDを入力してください。")
    login_id = _req(form, "login_id", "ログインIDを入力してください。")
    password = _req(form, "password", "パスワードを入力してください。")
    months = [m.strip() for m in _multi(form, "months") if _valid_month(m.strip())]
    if not months:
        months = default_kintai_months(today)
    # 重複除去 + 新しい順、上限まで
    seen = []
    for m in months:
        if m not in seen:
            seen.append(m)
    seen = sorted(seen, reverse=True)[:max_months]
    return KintaiParams(company, login_id, password, seen)


def _parse_date(value, default):
    # type: (str | None, _dt.date) -> _dt.date
    value = (value or "").strip()
    if not value:
        return default
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise ParamError("日付の形式が正しくありません。")


def _valid_month(value):
    # type: (str) -> bool
    if not value or len(value) != 7 or value[4] != "-":
        return False
    try:
        y, m = value.split("-")
        return 1 <= int(m) <= 12 and len(y) == 4
    except ValueError:
        return False


def _status_list(form):
    # type: (dict) -> list[str]
    return _multi(form, "statuses")


def _multi(form, key):
    # type: (dict, str) -> list[str]
    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        return list(getlist(key))
    value = form.get(key)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
