"""UI 多言語化 (日本語=既定 / English / Oʻzbekcha)。

日本語テキストをキーにして訳を引く。テンプレートでは `_("日本語")`、Python 側の
メッセージも同じ辞書で訳す。言語は Cookie (ブラウザ側) に保存し、サーバには保存しない。
"""
from __future__ import annotations

import re

from flask import g, redirect, request

from i18n_catalog import ENTRIES

LANGUAGES = [("ja", "日本語"), ("en", "English"), ("uz", "Oʻzbekcha")]
LANGUAGE_CODES = [c for c, _ in LANGUAGES]
DEFAULT_LANG = "ja"
COOKIE_NAME = "rr_lang"
ONE_YEAR = 365 * 24 * 3600
_INDEX = {"en": 0, "uz": 1}
_NUMERIC = {"n", "mb", "s", "m"}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

CATALOG = {}  # type: dict[str, tuple[str, str]]
for _row in ENTRIES:
    CATALOG[_row[0]] = (_row[1], _row[2])


def _compile(key):
    # type: (str) -> re.Pattern
    parts = []
    seen = set()
    pos = 0
    for m in _PLACEHOLDER.finditer(key):
        parts.append(re.escape(key[pos:m.start()]))
        name = m.group(1)
        if name in seen:
            parts.append("(?P=%s)" % name)
        else:
            seen.add(name)
            body = r"[\d,]+" if name in _NUMERIC else r".+?"
            parts.append("(?P<%s>%s)" % (name, body))
        pos = m.end()
    parts.append(re.escape(key[pos:]))
    return re.compile("^" + "".join(parts) + "$", re.DOTALL)


_PATTERNS = sorted(
    ((_compile(k), k) for k in CATALOG if _PLACEHOLDER.search(k)),
    key=lambda it: -len(_PLACEHOLDER.sub("", it[1])),
)


def normalize_lang(value):
    # type: (str | None) -> str
    return value if value in LANGUAGE_CODES else DEFAULT_LANG


def translate(text, lang=DEFAULT_LANG, **params):
    # type: (object, str, object) -> str
    if text is None:
        return ""
    source = str(text)
    lang = normalize_lang(lang)
    if lang == DEFAULT_LANG:
        return source.format(**params) if params else source
    if params:
        entry = CATALOG.get(source)
        template = entry[_INDEX[lang]] if entry else source
        return template.format(**params)
    entry = CATALOG.get(source)
    if entry:
        return entry[_INDEX[lang]]
    for pattern, key in _PATTERNS:
        found = pattern.match(source)
        if found:
            return CATALOG[key][_INDEX[lang]].format(**found.groupdict())
    return source


def current_lang():
    # type: () -> str
    return getattr(g, "lang", DEFAULT_LANG)


def init_app(app):
    # type: (object) -> None
    @app.before_request
    def _set_lang():  # noqa: ANN202
        g.lang = normalize_lang(request.cookies.get(COOKIE_NAME))

    @app.context_processor
    def _inject():  # noqa: ANN202
        lang = current_lang()
        current_url = request.path + (("?" + request.query_string.decode("utf-8")) if request.query_string else "")
        return {
            "_": lambda text, **kw: translate(text, lang, **kw),
            "lang": lang,
            "languages": LANGUAGES,
            "current_url": current_url,
        }

    @app.get("/lang/<code>")
    def set_language(code):  # noqa: ANN202
        target = _safe_next(request.args.get("next", "/"))
        resp = redirect(target, code=303)
        if code in LANGUAGE_CODES:
            resp.set_cookie(
                COOKIE_NAME, code, max_age=ONE_YEAR, httponly=True, samesite="Lax",
                secure=request.is_secure, path="/",
            )
        return resp


def _safe_next(target):
    # type: (str) -> str
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target
