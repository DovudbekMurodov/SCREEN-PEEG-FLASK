"""テスト用の擬似 楽楽精算 / 楽楽勤怠 (実セレクタに合わせた最小DOM)。本番では使わない。"""
from __future__ import annotations

import io
import json
import threading
from datetime import datetime
from urllib.parse import quote
from wsgiref.simple_server import make_server

from flask import Flask, Response, redirect, request
from openpyxl import Workbook

from tests.oneclick_fixtures import attendance_xlsx_bytes, expense_csv_bytes

def _cd(name, ascii_fallback):
    return "attachment; filename=%s; filename*=UTF-8''%s" % (ascii_fallback, quote(name))


TENANT = "qHKmc0veJHa"
SEISAN_LOGIN_ID = "AZ999999"
SEISAN_PASSWORD = "seisan-pass"
KINTAI_COMPANY = "PEEG"
KINTAI_LOGIN_ID = "AZ999999"
KINTAI_PASSWORD = "kintai-pass"


def _csv_bytes():
    return expense_csv_bytes()


def _csv_bytes_simple():
    header = (
        "ヘッダ情報:伝票No.(伝票No.),明細情報:明細日付(日付),明細情報:出発地(出発),"
        "明細情報:到着地(到着),明細情報:金額(金額)"
    )
    rows = [header, "10231,2026/09/01,東京,大阪,13500", "10231,2026/09/02,大阪,東京,13500"]
    return ("\r\n".join(rows) + "\r\n").encode("cp932")


def _xlsx_bytes(ym):
    return attendance_xlsx_bytes(ym)


def _xlsx_bytes_simple(ym):
    wb = Workbook()
    ws = wb.active
    ws.title = "一般"
    ws.append(["出勤簿（日別詳細） %s" % ym])
    ws.append([])
    ws.append(["社員番号", "氏名", "日付", "出勤時刻", "退勤時刻"])
    ws.append(["AH000135", "試験　一郎", "%s-01" % ym, "09:00", "18:00"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def create_mock():
    app = Flask("mock_raku")
    app.config["state"] = {"forbidden": [], "seisan_logins": 0, "kintai_logins": 0,
                           "kintai_remember": [], "kintai_list_calls": 0,
                           "empty_months": [],  # 出勤簿の行が0件の月 (テストが設定)
                           # 楽楽精算「ファイル出力」の挙動: ok=CSV / alert=該当なしをalert / page=該当なしを画面に表示
                           "seisan_export_mode": "ok",
                           "seisan_locked": False}  # True: ログインで「ロックされています」
    st = app.config["state"]

    @app.post("/__mock/forbidden")
    def forbidden():
        st["forbidden"].append(request.get_data(as_text=True))
        return {"ok": True}

    @app.get("/__mock/state")
    def state():
        return dict(st)

    # ----------------------------------------------------------------- 楽楽精算
    @app.get("/%s/ssooff" % TENANT)
    def seisan_login_page():
        return """<!doctype html><html lang=ja><head><meta charset=utf-8><title>ログイン</title></head>
<body><div id="d_login_input"><div><div>
<input id="loginId" name="loginId" type="text">
<input id="password" name="password" type="password">
</div></div></div>
<input id="submitBtn" type="submit" value="ログイン" onclick="doLogin()">
<script>
function doLogin(){var f=document.createElement('form');f.method='post';f.action='/%s/login';
var a=document.createElement('input');a.name='loginId';a.value=document.getElementById('loginId').value;
var b=document.createElement('input');b.name='password';b.value=document.getElementById('password').value;
f.appendChild(a);f.appendChild(b);document.body.appendChild(f);f.submit();}
</script></body></html>""" % TENANT

    @app.post("/%s/login" % TENANT)
    def seisan_login():
        if st["seisan_locked"]:
            return ("<html><body><div class='error'>このアカウントはロックされています。"
                    "管理者にお問い合わせください。</div></body></html>"), 200
        if request.form.get("loginId") == SEISAN_LOGIN_ID and request.form.get("password") == SEISAN_PASSWORD:
            st["seisan_logins"] += 1
            resp = redirect("/%s/sapTopPage/mainView" % TENANT, code=303)
            resp.set_cookie("seisan", "1")
            return resp
        return "<html><body><div class='error'>ログインIDまたはパスワードが正しくありません</div></body></html>", 200

    @app.get("/%s/sapTopPage/mainView" % TENANT)
    def seisan_top():
        if request.cookies.get("seisan") != "1":
            return redirect("/%s/ssooff" % TENANT, code=303)
        return """<html><body><h1>トップ</h1>
<a href="/%s/sapDcsvoutJiBumonDownload/initializeView">伝票データ出力(自部門)</a></body></html>""" % TENANT

    @app.get("/%s/sapDcsvoutJiBumonDownload/initializeView" % TENANT)
    def seisan_export():
        if request.cookies.get("seisan") != "1":
            return redirect("/%s/ssooff" % TENANT, code=303)
        return _seisan_export_page()

    @app.get("/%s/sapDcsvoutJiBumonDownload/output" % TENANT)
    def seisan_output():
        # 実サイト相当: 該当伝票が無いと同じ画面を再表示してエラー文言を出す
        if st["seisan_export_mode"] == "page":
            return _seisan_export_page('<div class="errorMessage">出力対象のデータが存在しません。</div>')
        return redirect("/%s/sapDcsvoutJiBumonDownload/download" % TENANT, code=303)

    def _seisan_export_page(error=""):
        statuses = ["承認依頼中", "仮払金精算待", "支払確定待", "支払確定済", "差戻し", "取下げ", "否認", "対象外"]
        ids = {"承認依頼中": "denpyoStatus_0", "仮払金精算待": "denpyoStatus_-2", "支払確定待": "denpyoStatus_-1",
               "支払確定済": "denpyoStatus_-9", "差戻し": "denpyoStatus_1", "取下げ": "denpyoStatus_2",
               "否認": "denpyoStatus_-99", "対象外": "denpyoStatus_-98"}
        checks = "".join(
            '<label><input type="checkbox" id="%s" %s>%s</label>'
            % (ids[s], "checked" if s == "承認依頼中" else "", s)
            for s in statuses
        )
        dates = lambda: "".join('<input class="positiveIntTextBox imeOff d_widthTxt4" type="text">' for _ in range(6))
        mode = st["seisan_export_mode"]
        onclick = ("alert('出力対象のデータが存在しません。')" if mode == "alert"
                   else "location.href='/%s/sapDcsvoutJiBumonDownload/output'" % TENANT)
        return """<html><body><h1>伝票データ出力【自部門】</h1>%s
<p class="message">※ 申請日を指定しない場合、期間の制限はありません。</p>
<div id="d_master_top">
<select class="szb-select-small-auto"><option selected>出張精算(MEBA)（出張精算）</option><option>経費精算</option></select>
<label><input type="checkbox" id="komokuOutputFlag_1">項目名を出力する</label>
<div>申請日 %s</div>
<div>承認完了日 %s</div>
<div>伝票状態 %s</div>
<button class="imgButton output" onclick="%s">ファイル出力</button>
</div></body></html>""" % (error, dates(), dates(), checks, onclick)

    @app.get("/%s/sapDcsvoutJiBumonDownload/download" % TENANT)
    def seisan_download():
        name = "出張精算_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S")
        return Response(
            _csv_bytes(), mimetype="text/csv",
            headers={"Content-Disposition": _cd(name, "seisan.csv")},
        )

    # ----------------------------------------------------------------- 楽楽勤怠
    @app.get("/app/login")
    def kintai_login_page():
        return """<!doctype html><html lang=ja><head><meta charset=utf-8></head><body>
<div id="login-app"><form action="/app/login.authenticate" method="post">
<label>お客様ID​</label><input name="customer_id" type="text">
<label>ログインID​</label><input name="login_id" type="text">
<label>パスワード​</label><input name="password" type="password">
<label><input type="checkbox" name="remember" checked>ログイン情報を保持する</label>
<button type="submit">ログイン</button></form></div></body></html>"""

    @app.post("/app/login.authenticate")
    def kintai_login():
        f = request.form
        if f.get("customer_id") == KINTAI_COMPANY and f.get("login_id") == KINTAI_LOGIN_ID and f.get("password") == KINTAI_PASSWORD:
            st["kintai_logins"] += 1
            st["kintai_remember"].append(bool(f.get("remember")))
            resp = redirect("/app/home", code=303)
            resp.set_cookie("kintai", "1")
            return resp
        return "<html><body><div class='text-error'>正しくありません</div></body></html>", 200

    @app.get("/app/home")
    def kintai_home():
        if request.cookies.get("kintai") != "1":
            return redirect("/app/login", code=303)
        return """<html><body><div id="appHeader"><nav><ul>
<li class="base_header_tab"><button>勤怠管理</button></li></ul>
<ul><li id="attendanceManager">出勤簿管理</li></ul></nav></div></body></html>"""

    @app.get("/app/attendancemanagement")
    def kintai_att():
        if request.cookies.get("kintai") != "1":
            return redirect("/app/login", code=303)
        ym = request.cookies.get("ym", "2026-09")
        # 実サイト同様の SPA: 開いた月は読込済み。月を切り替えると矢印が
        # getMonthlyList(front-api) を叩いてその月を読み込む (loaded_ym で表現)。
        loaded = request.cookies.get("loaded_ym")
        if not loaded:
            loaded = ym  # 初回オープン = その月は描画済み
        y, m = ym.split("-")
        header = "%s年%s月( %s年%s月01日 - %s年%s月30日 )" % (y, m, y, m, y, m)
        # 実サイト: その月の行が0件だとグリッド(見出し・ページャ・Excelアイコン)を一切描画しない。
        if loaded == ym and ym not in st["empty_months"]:
            grid = """  <div class="footer"><div class="footer_right">
    <button class="js-forbidden" onclick="fb(this)">次の承認者へ</button>
    <button class="js-forbidden" onclick="fb(this)">差し戻しへ</button>
    <img title="勤怠情報のExcelエクスポート" src="/x/excel_download.svg" onclick="openDlg()">
  </div></div>
  <div class="dialog_window excel_download_dialog" id="dlg" style="display:none">
    <label class="radio_button_label"><input type="radio" name="export_type" value="1" checked style="display:none"><span class="radio_button_text">月別詳細</span></label>
    <label class="radio_button_label"><input type="radio" name="export_type" value="2" style="display:none"><span class="radio_button_text">日別詳細</span></label>
    <footer><button class="label_button button_positive" onclick="startExport()">エクスポート</button><button class="label_button button_negative">キャンセルする</button></footer>
  </div>
  <div class="dialog_window file_export_modal" id="modal" style="display:none">
    <div id="modaltext">処理中</div>
    <footer><a class="file_link" href="/app/export/download"><button class="button_positive" id="dlbtn" disabled onclick="location.href='/app/export/download'">ダウンロード</button></a>
    <button onclick="document.getElementById('modal').style.display='none'">閉じる</button></footer>
  </div>"""
        else:
            grid = '  <div class="empty_grid"><!-- グリッド未読込 --></div>'
        resp = Response("""<html><body><div id="appHeader"></div>
<div id="attendance_management">
  <div class="change_date_control"><div class="change_prev" onclick="chg(-1)"><img src="/x/next.svg"></div><div class="display_date">%s</div><div class="change_next" onclick="chg(1)"><img src="/x/next.svg"></div></div>
%s
</div>
<script>
function fb(b){fetch('/__mock/forbidden',{method:'POST',body:b.textContent});}
/* 実サイト同様、表示時と矢印クリック時に月リストAPI(front-api)が発火する。 */
function loadList(){return fetch('/app/front-api/attendanceManagement.getMonthlyList',{method:'POST'});}
window.addEventListener('load', function(){ loadList(); });
function chg(d){fetch('/app/attendancemanagement/month',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delta:d})}).then(loadList).then(function(r){return r.json();}).then(function(){setTimeout(function(){location.href='/app/attendancemanagement';},300);});}
function openDlg(){document.getElementById('dlg').style.display='block';}
function startExport(){document.getElementById('dlg').style.display='none';
 var mo=document.getElementById('modal');mo.style.display='block';
 setTimeout(function(){document.getElementById('modaltext').textContent='処理完了 100%%';
  document.getElementById('dlbtn').disabled=false;},400);}
</script></body></html>""" % (header, grid))
        resp.set_cookie("ym", ym)
        resp.set_cookie("loaded_ym", loaded)
        return resp

    @app.post("/app/attendancemanagement/month")
    def kintai_month():
        data = request.get_json(force=True)
        cur = request.cookies.get("ym", "2026-09")
        y, m = [int(x) for x in cur.split("-")]
        idx = y * 12 + (m - 1) + int(data.get("delta", 0))
        ny, nm = divmod(idx, 12)
        target = "%04d-%02d" % (ny, nm + 1)
        resp = Response("{}", mimetype="application/json")
        resp.set_cookie("ym", target)
        # 月を変えた直後はまだ未読込 (getMonthlyList が来るまで)。
        resp.set_cookie("loaded_ym", "")
        return resp

    @app.post("/app/front-api/attendanceManagement.getMonthlyList")
    def kintai_get_monthly_list():
        # SPA が月グリッドを読み込むAPI。表示中の月を読込済みにし、行数を返す
        # (データ無しの月は total_count=0 / list=[] → 画面はグリッドを描画しない)。
        st["kintai_list_calls"] = st.get("kintai_list_calls", 0) + 1
        ym = request.cookies.get("ym", "2026-09")
        empty = ym in st["empty_months"]
        # 実応答の形 (画面JS: res.data.data.list_data / .pager.total_count / .period_list ...)。
        # period_list は空の月でも要素を持つ → 行数と誤認しない実装であることをテストで担保する。
        body = {"data": {
            "list_data": [] if empty else [{"employee_id": "AH000135", "name": "試験　一郎"}],
            "pager": {"total_count": 0 if empty else 37, "limit": 20, "offset": 0},
            "period_list": [{"ym": "2026-09"}, {"ym": "2026-08"}, {"ym": "2026-07"}],
            "index": {"page": 1}, "filter": {}, "date": ym + "-01", "lang": "ja",
        }}
        resp = Response(json.dumps(body, ensure_ascii=False), mimetype="application/json")
        resp.set_cookie("loaded_ym", ym)
        return resp

    @app.get("/app/export/download")
    def kintai_dl():
        ym = request.cookies.get("ym", "2026-09")
        name = "出勤簿_日別詳細_%s.xlsx" % datetime.now().strftime("%Y%m%d%H%M%S")
        return Response(
            _xlsx_bytes(ym),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": _cd(name, "kintai.xlsx")},
        )

    return app


class MockServer:
    def __init__(self):
        self.app = create_mock()
        self.httpd = make_server("127.0.0.1", 0, self.app)
        self.port = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def state(self):
        return self.app.config["state"]

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()

    def seisan_base(self):
        return "%s/%s/" % (self.base, TENANT)
