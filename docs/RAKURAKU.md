# 楽楽精算 / 楽楽勤怠 自動ダウンロード機能

既存のチェックシート生成に加え、ナビの「楽楽精算」「楽楽勤怠」から各システムに
自動ログインしてファイルを取得できます。取得したファイルをチェックシート画面に
アップロードすれば従来どおり判定できます。

- **サーバにデータを保存しません。** 認証情報はフォームで受け取り、メモリ上で
  Playwright に渡すだけで、生成物は一時ファイル→配信後に破棄します。ログインID/
  パスワードは各端末のブラウザ (localStorage) にのみ保存されます (「認証情報をこの
  端末に保存する」のチェックで制御、「消去」ボタンで削除)。
- **表示言語** は 日本語 / English / Oʻzbekcha (ナビ右上、Cookie に保存)。

## 構成 (追加分)

| 追加ファイル | 役割 |
|---|---|
| `rakuraku/` | Playwright クライアント (`seisan.py`/`kintai.py`)、セレクタ (`selectors.py`)、ブラウザ起動 (`browser.py`)、ロケータ/クリック安全弁 (`locators.py`)、パラメータ (`params.py`)、エラー (`errors.py`)、ログ秘匿 (`redact.py`)、デバッグ実行 (`debug.py`) |
| `rakuraku_web.py` | Flask Blueprint (`/seisan`, `/seisan/run`, `/kintai`, `/kintai/run`) |
| `i18n.py` / `i18n_catalog.py` | UI 多言語化 (`/lang/<code>`) |
| `templates/base.html` ほか | 共通ナビ + Seisan/Kintai 画面 |
| `static/rakuraku.css` / `static/rakuraku.js` | 追加スタイル / localStorage 保存・fetch ダウンロード |

既存の `app.py` は **登録の数行のみ** 追加。`src/` (判定エンジン) と `/run` の処理は不変。

## ローカルで試す

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium      # ローカルは同梱Chromium
PORT=5001 .venv/bin/python app.py          # http://127.0.0.1:5001/seisan
```

セレクタを実サイトで確認する (パスワードは getpass 入力、ログに出ない):

```bash
.venv/bin/python -m rakuraku.debug seisan --headed --slowmo 250
.venv/bin/python -m rakuraku.debug kintai --headed --month 2026-08
```

## PythonAnywhere (本番)

**重要:** この機能はサーバ側でブラウザを動かすため、**有料プラン**が必要です。
無料プランでは (1) 外部サイトへの接続がallowlistで塞がれ `rakurakuseisan.jp` /
`tms.kinnosuke.jp` に届かず、(2) Playwright が使えません。無料のままでも既存の
チェックシート機能はそのまま動きます。

有料プランでの設定:
1. 仮想環境に `pip install playwright` (※ `playwright install` は不可)。
2. 環境変数 `RR_CHROMIUM_PATH=/usr/bin/chromium` を設定 (システム同梱Chromiumを使う)。
3. Web タブで Reload。
4. 各ダウンロードは Web リクエストの **5分制限** 内に収める (Seisan/Kintai を別ページ
   に分けているため通常は問題なし。勤怠が遅い場合は対象月を1つずつ)。
5. RakuRaku 側がアクセス元IPを制限している/2段階認証が出る場合はクラウドから実行
   できないため、ローカル実行に切り替える。

## 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `RR_SEISAN_BASE_URL` | `https://rswaltz.rakurakuseisan.jp/qHKmc0veJHa/` | 楽楽精算テナントURL |
| `RR_KINTAI_LOGIN_URL` | `https://tms.kinnosuke.jp/app/login` | 楽楽勤怠ログイン |
| `RR_KINTAI_ATTENDANCE_URL` | `https://tms.kinnosuke.jp/app/attendancemanagement` | 出勤簿管理 |
| `RR_KINTAI_COMPANY_CODE` | `PEEG` | お客様ID (画面の初期値) |
| `RR_CHROMIUM_PATH` | (自動) | Chromium実行ファイル (PythonAnywhereは `/usr/bin/chromium`) |
| `RR_HEADLESS` | `1` | `0` でヘッド付き (デバッグ) |
| `RR_KINTAI_MAX_MONTHS` | `3` | 1回で取得する最大月数 |
| `RR_OUTBOUND_PROXY_URL` | (なし) | 社内プロキシ |

## 未確定 / 要確認

- **楽楽勤怠の「前月」ボタン**のセレクタは PAD になく、`rakuraku/selectors.py` の
  `kintai_prev_month` は推測。実アカウントで `rakuraku.debug kintai --month <先月>` を
  実行して確認・修正が必要 (`<< < 1 2 > >>` は従業員のページ送りで月移動ではない)。
- **申請日の入力欄**は「申請日」ラベル直後の6個を使う想定。実DOMで確認のこと。
- テスト用に共有された認証情報は、確認後に必ず変更してください。
- 既存リポジトリの `src/secrets_local.py`・平文マスタパスワードは本機能とは別の
  既存課題として残っています (今回は変更していません)。

## 楽楽勤怠: データが無い月の扱い

- 月の切替は上部の ‹ › (`.change_prev` / `.change_next`)。クリックすると SPA が
  `/front-api/attendanceManagement.getMonthlyList` を叩き、行があればグリッドと
  Excel エクスポートアイコンを描画する (絞り込みボタンを押す必要はない)。
- 行が 0 件の月は、サイトがグリッド (見出し・ページャ・アイコン) を一切描画しない。
  自動処理はこれを **その月はデータ無し** と判定し、致命エラーにせず ⚠ スキップして
  残りの月を出力する。選択した全月が 0 件のときだけエラー
  (「選択した月の出勤簿データがありませんでした。」)。
- `RR_DEBUG_DUMP=1` のとき `kintai_net_*.log` に front-api の呼び出しと
  getMonthlyList の行数 (`LIST count=...`) が残るので、0 件かセレクタ崩れかを切り分けられる。

## ワンクリック実行 (`/oneclick`)

楽楽精算の出力 → 楽楽勤怠の出力 → チェックシート作成 を1回のクリックで行う画面 (メニューの先頭)。

1. 楽楽精算から出張精算CSV (申請日の期間は画面で変更可、既定は直近1か月) を取得。
2. 出勤簿の月: **当月・前月は必ず取得**し、CSVの「明細日付」にそれより前の月があれば追加する (`plan_kintai_months`、新しい順、最大 `RR_ONECLICK_MAX_MONTHS`=4 か月。超えた月は取得せず画面に表示)。
3. 楽楽勤怠から各月の出勤簿（日別詳細）を取得 (データ無しの月は ⚠ スキップ)。
4. 出勤簿をエンジンが読める形に整形 (`rakuraku/oneclick.py: normalize_attendance_xlsx`) して、
   既存のチェックシート処理 (`src/main.py`) を `--config {"attendance_paths": [...全月]}` で実行。
5. 結果 (判定の件数・Excel・HTMLレポート・取得した元データ) を画面に表示。サーバの作業フォルダは即削除。

**出勤簿の整形について:** 楽楽勤怠の出勤簿（日別詳細）は 1行目がタイトル・ヘッダが3行目・時刻が `'HH:MM'` 文字列のため、
`src/` のエンジンはそのままでは1行も読めず、全件「未確認（勤怠データ欠落）」になっていた。
ヘッダ行を先頭へ移し、時刻列を 日付+時刻 (24:00超は翌日) に変換する。エンジン形式のファイルは変更しない。
手動アップロード (`/run`) の出勤簿にも同じ整形を適用している (`app.py: _normalize_attendance`)。
