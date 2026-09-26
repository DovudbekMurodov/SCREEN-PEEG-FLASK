"""実サイトでのセレクタ確認用 (headed) 実行ツール。本番では使わない。

  python -m rakuraku.debug seisan --headed --slowmo 250
  python -m rakuraku.debug kintai --headed --month 2026-08

パスワードは getpass で入力し、ログには出さない。RR_ENV=prod では動かない。
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import sys

from rakuraku.browser import browser_session
from rakuraku.kintai import KintaiClient
from rakuraku.params import KintaiParams, SeisanParams, default_seisan_range
from rakuraku.seisan import SeisanClient

SEISAN_BASE_URL = os.environ.get("RR_SEISAN_BASE_URL", "https://rswaltz.rakurakuseisan.jp/qHKmc0veJHa/")
KINTAI_LOGIN_URL = os.environ.get("RR_KINTAI_LOGIN_URL", "https://tms.kinnosuke.jp/app/login")
KINTAI_ATTENDANCE_URL = os.environ.get("RR_KINTAI_ATTENDANCE_URL", "https://tms.kinnosuke.jp/app/attendancemanagement")


def _log(msg, lvl="info"):
    print("[%s] %s" % (lvl, msg))


def main(argv=None):
    parser = argparse.ArgumentParser(description="楽楽 自動化デバッグ (開発用)")
    parser.add_argument("service", choices=["seisan", "kintai"])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--slowmo", type=int, default=0)
    parser.add_argument("--month", help="kintai: YYYY-MM (既定は今月)")
    parser.add_argument("--company", default=os.environ.get("RR_KINTAI_COMPANY_CODE", "PEEG"))
    parser.add_argument("--login-id")
    args = parser.parse_args(argv)

    if os.environ.get("RR_ENV") == "prod":
        print("RR_ENV=prod では実行しません", file=sys.stderr)
        return 2

    login_id = args.login_id or input("ログインID: ")
    password = getpass.getpass("パスワード（表示されません）: ")
    out_dir = os.path.join(os.getcwd(), "rakuraku_debug")
    os.makedirs(out_dir, exist_ok=True)

    with browser_session(headless=not args.headed, slow_mo_ms=args.slowmo) as ctx:
        if args.service == "seisan":
            dfrom, dto = default_seisan_range()
            client = SeisanClient(ctx, SEISAN_BASE_URL, log=_log)
            name, data = client.download(
                SeisanParams(login_id, password, "自部門", "出張精算(MEBA)", dfrom, dto, ["承認依頼中"])
            )
            _save(out_dir, name, data)
            client.logout()
        else:
            month = args.month or dt.date.today().strftime("%Y-%m")
            client = KintaiClient(ctx, KINTAI_LOGIN_URL, KINTAI_ATTENDANCE_URL, log=_log)
            for name, data in client.download(KintaiParams(args.company, login_id, password, [month]),
                                              progress=lambda t: print("  " + t)):
                _save(out_dir, name, data)
            client.logout()
    return 0


def _save(out_dir, name, data):
    path = os.path.join(out_dir, name)
    with open(path, "wb") as fh:
        fh.write(data)
    print("saved:", path)


if __name__ == "__main__":
    raise SystemExit(main())
