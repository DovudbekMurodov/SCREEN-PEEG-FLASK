"""利用者に見せるエラー文言が全て en/uz に翻訳されていること (ソースから自動収集)。"""
from __future__ import annotations

import ast
import glob
import inspect

import pytest

import rakuraku.errors as errors
from i18n import CATALOG


def _user_messages_in_source():
    found = set()
    for path in glob.glob("rakuraku/*.py") + ["rakuraku_web.py"]:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "user_message" and isinstance(node.value, ast.Constant):
                found.add(node.value.value)
            # translate("...", lang) の第1引数
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "translate"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                found.add(node.args[0].value)
    for _, cls in inspect.getmembers(errors, inspect.isclass):
        if issubclass(cls, errors.RakuError):
            found.add(cls.user_message)
    return sorted(m for m in found if isinstance(m, str) and m)


@pytest.mark.parametrize("message", _user_messages_in_source())
def test_every_user_message_is_translated(message):
    assert message in CATALOG, "missing translation: %s" % message
    en, uz = CATALOG[message]
    assert en and uz


def test_step_labels_translated():
    from i18n import translate
    from rakuraku.kintai import KintaiClient
    from rakuraku.seisan import SeisanClient

    labels = [l for _, l in SeisanClient.STEPS] + [l for _, l in KintaiClient.steps_for(["2026-09", "2026-08"])]
    labels.append("チェックシートを作成")
    for label in labels:
        for lang in ("en", "uz"):
            assert translate(label, lang) != label, (lang, label)
