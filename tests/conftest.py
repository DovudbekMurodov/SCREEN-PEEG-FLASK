from __future__ import annotations

import pytest

from tests.mock_raku import MockServer


@pytest.fixture(scope="session")
def mock():
    server = MockServer().start()
    yield server
    server.stop()


@pytest.fixture
def raku(mock):
    st = mock.state
    st["forbidden"][:] = []
    st["seisan_logins"] = 0
    st["kintai_logins"] = 0
    st["kintai_remember"][:] = []
    st["kintai_list_calls"] = 0
    st["empty_months"][:] = []  # テストごとに「データ無しの月」をリセット
    st["seisan_export_mode"] = "ok"
    st["seisan_locked"] = False
    return mock


@pytest.fixture
def client():
    import app as appmod

    appmod.app.config.update(TESTING=True)
    with appmod.app.test_client() as c:
        yield c


def chromium_available():
    from rakuraku.browser import playwright_available

    return playwright_available()
