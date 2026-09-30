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

## 楽楽精算: 該当伝票が無いとき / ログインできないとき

- 条件（申請日・伝票状態）に該当する伝票が無いと、楽楽精算は「ファイル出力」を押してもCSVを出さず、
  メッセージ（alert または画面上の文言）を出すだけ。クリック前後の表示を比べ、新しく出た「該当なし」
  文言を検知したら約5秒で `NO_DATA`（該当する伝票がありません）として止める。以前は見落として
  180秒待ち `DOWNLOAD_TIMEOUT` になっていた。
- ログイン失敗・ロック・該当なしなどでは、楽楽側の実際の表示文言をエラーメッセージに
  「（楽楽精算の表示：「…」）」として添える。IDとパスワードの誤りか、ツールの問題かを画面だけで判断できる。
  ロックは `ACCOUNT_LOCKED` として別メッセージ。文言はサーバのログには残さない（エラーコードのみ）。

## 同時実行・接続切れ・安全対策

- ブラウザ処理は同時に `RR_MAX_CONCURRENT_JOBS`（既定 1）件まで。1 GB のサーバでは Chromium 2つでメモリ上限
  （systemd `MemoryMax=650M`）を超えるため。空きが無いと画面に「ほかの方の処理が終わるまでお待ちください…」を出して
  順番待ちし、待ち人数が `RR_MAX_WAITING_JOBS`（既定 3）を超えるか `RR_QUEUE_WAIT_SECONDS`（既定 900）を過ぎると
  「混み合っています」を返す。待ちも gunicorn のスレッドを使うので、スレッド数は 実行1 + 待ち3 + ページ表示用 の余裕を持たせる（本番 8）。
- 進捗ストリームは 15 秒ごとにコメント行（`: ping`）を送る（社内プロキシの無通信切断対策）。
- 画面を閉じるなどで接続が切れたら、次のステップや待ちループの境界で処理を打ち切り、実行枠を空ける。
- パスワードはジョブの間だけログのマスク用に保持し、終われば消す（サーバのメモリに残し続けない）。
- HTMLレポートは申請内容（利用者が入力した文字）を含むため、「HTMLレポートを開く」はサイトと別オリジンの
  sandbox iframe で表示する（保存済みのID・パスワードに届かないように）。
- チェックシート作成は `TZ=Asia/Tokyo` で動かす（サーバが UTC でも生成日時・ファイル名は日本時間）。

## 実行中の画面（ライブ表示）

- 実行ボタンを押すと「自動操作の画面（リアルタイム）」のウィンドウが開き、サーバ上のブラウザの画面をそのまま表示する
  （PADで実行を見ているのと同じ感覚）。閲覧のみで、クリック・入力をサーバへ送る経路は無い。
- 仕組み: Chromium の CDP screencast（`rakuraku/live.py`）で描き変わるたびに JPEG を受け取り、SSE の `frame`
  イベントで送る。最新の1コマだけをメモリに持ち、約0.4秒ごとに送る（`LIVE_FRAME_INTERVAL`）。保存はしない。
- 画面側から `live=1` を送ったときだけ有効。終了すると右下に小さく表示し、「実行中の画面を表示」で開き直せる。

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
