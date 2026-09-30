"""ロケータ解決: 候補を順に試し、危険なボタン (承認・差戻し等) のクリックを拒否する。"""
from __future__ import annotations

import re
import threading
import time
import unicodedata

try:
    from playwright.sync_api import Error as PlaywrightError
except ImportError:  # playwright 未インストール (無料プラン等) でも Blueprint を登録できるようにする
    class PlaywrightError(Exception):  # type: ignore[no-redef]
        pass

from rakuraku.errors import ForbiddenActionBlocked, JobCancelled, SelectorNotFound

# 接続が切れたジョブを、長い待ち (ダウンロード待ち等) の途中でも打ち切るための合図。
# ジョブを動かすスレッドごとに set_cancel(event) で設定し、待ちループで check_cancel() する。
_local = threading.local()


def set_cancel(event):
    # type: (object) -> None
    _local.cancel = event


def check_cancel():
    # type: () -> None
    ev = getattr(_local, "cancel", None)
    if ev is not None and ev.is_set():
        raise JobCancelled("client disconnected")

# 楽楽勤怠 出勤簿管理で Excel アイコンと同じ枠にある操作系ボタン。絶対に押さない。
FORBIDDEN_CLICK = re.compile(r"次の承認者へ|最終承認|差し?戻し|否認|取下げ|取り下げ|削除|承認する")


def nfkc(text):
    # type: (str) -> str
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _page_of(scope):
    return scope if hasattr(scope, "wait_for_timeout") and not hasattr(scope, "count") else scope.page


def resolve(scope, candidates, timeout_ms=10_000, service=None, name="element", visible=True):
    # type: (object, list, int, str | None, str, bool) -> object
    """candidates は locator を作る callable のリスト。1件だけ一致する最初のものを返す。

    visible=True: 可視要素のみ (既定)。カスタム部品で input が非表示の場合は
    visible=False にして隠れた要素も対象にする (force/JS で操作する)。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    page = scope if hasattr(scope, "wait_for_timeout") else scope.page
    while True:
        check_cancel()
        for make in candidates:
            try:
                loc = make(scope)
                if visible:
                    loc = loc.filter(visible=True)
                if loc.count() == 1:
                    return loc
            except PlaywrightError:
                continue
        if time.monotonic() >= deadline:
            raise SelectorNotFound("%s: no unique match" % name, service=service)
        page.wait_for_timeout(200)


def exists(scope, candidates):
    # type: (object, list) -> bool
    for make in candidates:
        try:
            if make(scope).filter(visible=True).count() >= 1:
                return True
        except PlaywrightError:
            continue
    return False


def wait_for_any(page, conditions, timeout_ms):
    # type: (object, dict, int) -> str
    """conditions は {key: () -> bool}。最初に真になった key を返す。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        check_cancel()
        for key, pred in conditions.items():
            try:
                if pred():
                    return key
            except PlaywrightError:
                continue
        if time.monotonic() >= deadline:
            raise TimeoutError("none of %s within %d ms" % (list(conditions), timeout_ms))
        page.wait_for_timeout(250)


def element_text(loc):
    # type: (object) -> str
    parts = []
    for getter in (
        lambda: loc.inner_text(timeout=2000),
        lambda: loc.get_attribute("value", timeout=2000),
        lambda: loc.get_attribute("title", timeout=2000),
        lambda: loc.get_attribute("aria-label", timeout=2000),
    ):
        try:
            value = getter()
        except PlaywrightError:
            value = None
        if value:
            parts.append(value)
    return " ".join(parts)


def safe_click(loc, timeout_ms=15_000, service=None):
    # type: (object, int, str | None) -> None
    text = element_text(loc)
    if FORBIDDEN_CLICK.search(text):
        raise ForbiddenActionBlocked("refused to click: %r" % text, service=service)
    loc.click(timeout=timeout_ms)


def robust_click(loc, timeout_ms=8_000, service=None):
    # type: (object, int, str | None) -> None
    """通常クリックがオーバーレイ(pointer-events横取り)等でタイムアウトしたら
    JS の el.click() で実行する。押す前に FORBIDDEN 判定は必ず行う。"""
    text = element_text(loc)
    if FORBIDDEN_CLICK.search(text):
        raise ForbiddenActionBlocked("refused to click: %r" % text, service=service)
    try:
        loc.click(timeout=timeout_ms)
    except PlaywrightError:
        loc.evaluate("el => el.click()")


def set_checkbox(loc, state):
    # type: (object, bool) -> None
    """カスタム部品 (szb-checkbox など) でも確実に ON/OFF する。

    通常の check()/uncheck() で状態が変わらない場合、ラベルのクリック→JSで直接
    checked を設定し change イベントを発火させる (フォーム送信値はこれで決まる)。
    """
    try:
        if loc.is_checked() == state:
            return
    except PlaywrightError:
        pass
    try:
        if state:
            loc.check(force=True)
        else:
            loc.uncheck(force=True)
        if loc.is_checked() == state:
            return
    except PlaywrightError:
        pass
    try:
        loc.evaluate(
            "(el, val) => {"
            " if (el.checked === val) return;"
            " var l = el.id ? document.querySelector('label[for=\"' + el.id + '\"]') : null;"
            " if (l) { l.click(); }"
            " if (el.checked !== val) { el.checked = val; }"
            " el.dispatchEvent(new Event('input', {bubbles:true}));"
            " el.dispatchEvent(new Event('change', {bubbles:true})); }",
            state,
        )
    except PlaywrightError:
        pass


def select_option_fuzzy(select, wanted):
    # type: (object, str) -> str
    """NFKC 正規化して部分一致するオプションを選ぶ (半角/全角の違いを吸収)。"""
    options = select.locator("option")
    labels = options.all_inner_texts()
    key = nfkc(wanted)
    for i, label in enumerate(labels):
        if key and key in nfkc(label):
            value = options.nth(i).get_attribute("value")
            if value is not None:
                select.select_option(value=value)
            else:
                select.select_option(label=label)
            return label.strip()
    raise SelectorNotFound("option containing %r not found; options=%s" % (wanted, labels))


# 画面に出ているエラー/お知らせの文言を拾う候補。楽楽側の実際の表示を利用者に見せるために使う。
MESSAGE_CSS = (
    "[role=alert], .error, .errors, .errorMessage, .errMsg, .text-error, "
    "[class*='error'], [class*='Error'], [class*='alert'], [class*='warn'], [class*='message'], [class*='Message']"
)
# 新しく出た文言のうち「失敗・該当なし」を示すもの (「処理中…」などの一時表示は除く)
ERRORISH = re.compile(
    r"エラー|できません|ありません|存在しません|見つかりません|失敗|入力してください|選択してください"
    r"|正しくありません|誤り|超え|ロック"
)

_MESSAGES_JS = """(css) => {
  const out = [];
  for (const el of document.querySelectorAll(css)) {
    if (!el.getClientRects().length) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 200 || out.includes(t)) continue;
    out.push(t);
    if (out.length >= 20) break;
  }
  return out;
}"""


def visible_messages(page, css=MESSAGE_CSS):
    # type: (object, str) -> list
    """表示中のメッセージ文言 (空白を詰めた短い文字列) のリスト。遷移中などで取れなければ []。"""
    try:
        return list(page.evaluate(_MESSAGES_JS, css) or [])
    except Exception:  # noqa: BLE001
        return []


def first_text(scope, candidates):
    # type: (object, list) -> str
    """候補のうち最初に見つかった可視要素の文言 (空白を詰める)。無ければ ""。"""
    for make in candidates:
        try:
            loc = make(scope).filter(visible=True)
            if loc.count() >= 1:
                return re.sub(r"\s+", " ", loc.first.inner_text(timeout=2000)).strip()[:200]
        except PlaywrightError:
            continue
    return ""
