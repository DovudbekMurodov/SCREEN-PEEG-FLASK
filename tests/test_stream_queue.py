"""同時実行の順番待ち・無通信対策 (heartbeat)・接続切れでの打ち切り。ブラウザは使わない。"""
from __future__ import annotations

import contextlib
import threading
import time

import pytest

import rakuraku_web

FORM = {"login_id": "AZ999999", "password": "x"}


@contextlib.contextmanager
def _fake_session(**kwargs):
    yield object()


class _SlowSeisan:
    from rakuraku.seisan import SeisanClient as _Real
    STEPS = _Real.STEPS
    delay = 0.0
    steps_done = []

    def __init__(self, *a, **k):
        pass

    def download(self, params, on_step=None):
        for key in ("login", "export", "download"):
            on_step(key, "running")
            time.sleep(self.delay)
            on_step(key, "done")
            _SlowSeisan.steps_done.append(key)
        return "出張精算_x.csv", "伝票No\n1\n".encode("cp932")

    def logout(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(rakuraku_web, "playwright_available", lambda: True)
    monkeypatch.setattr(rakuraku_web, "browser_session", _fake_session)
    monkeypatch.setattr(rakuraku_web, "SeisanClient", _SlowSeisan)
    monkeypatch.setattr(rakuraku_web, "_job_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(rakuraku_web, "WAIT_POLL_SECONDS", 0.2)
    _SlowSeisan.delay = 0.0
    _SlowSeisan.steps_done = []
    return rakuraku_web


def test_second_user_waits_for_free_slot_then_runs(client, fake):
    fake._job_slots.acquire()  # 別の人が実行中
    threading.Timer(1.0, fake._job_slots.release).start()
    body = client.post("/seisan/stream", data=FORM).get_data(as_text=True)
    assert "event: wait" in body and "ほかの方の処理が終わるまでお待ちください" in body
    assert "event: file" in body and "event: error" not in body
    assert fake._job_slots.acquire(blocking=False)  # 終了後に枠は返っている


def test_gives_up_after_queue_limit(client, fake, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "QUEUE_WAIT_SECONDS", 0.5)
    fake._job_slots.acquire()
    try:
        body = client.post("/seisan/stream", data=FORM).get_data(as_text=True)
    finally:
        fake._job_slots.release()
    assert "event: error" in body and "ほかの方の処理が続いている" in body
    assert "event: file" not in body


def test_wait_message_translated(client, fake):
    client.get("/lang/en")
    fake._job_slots.acquire()
    threading.Timer(0.6, fake._job_slots.release).start()
    body = client.post("/seisan/stream", data=FORM).get_data(as_text=True)
    assert "Waiting for another user" in body


def test_heartbeat_sent_while_idle(client, fake, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "HEARTBEAT_SECONDS", 0.1)
    _SlowSeisan.delay = 0.4
    body = client.post("/seisan/stream", data=FORM).get_data(as_text=True)
    assert ": ping" in body and "event: file" in body


def test_client_disconnect_stops_job_and_frees_slot(client, fake):
    _SlowSeisan.delay = 0.5
    resp = client.post("/seisan/stream", data=FORM, buffered=False)
    first = next(iter(resp.response))  # steps イベントだけ受け取って切断
    assert "event: steps" in (first.decode() if isinstance(first, bytes) else first)
    resp.close()
    freed = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not freed:
        freed = fake._job_slots.acquire(blocking=False)
        time.sleep(0.1)
    assert freed  # 枠が返り、次の人が実行できる
    assert len(_SlowSeisan.steps_done) < 3  # 最後まで走らずに打ち切られた


def test_non_stream_run_also_takes_slot(client, fake, monkeypatch):
    monkeypatch.setattr(rakuraku_web, "NON_STREAM_WAIT_SECONDS", 0.3)

    class _Plain(_SlowSeisan):
        def download(self, params, on_step=None):
            return "出張精算_x.csv", "伝票No\n1\n".encode("cp932")

    monkeypatch.setattr(rakuraku_web, "SeisanClient", _Plain)
    fake._job_slots.acquire()
    try:
        r = client.post("/seisan/run", data=FORM)
    finally:
        fake._job_slots.release()
    assert r.status_code == 502 and "ほかの方の処理が続いている" in r.get_data(as_text=True)


def test_queue_full_answers_busy_immediately(client, fake, monkeypatch):
    # 待ち人数の上限を超えたら待たずに「混み合っています」(スレッドを使い切らないため)
    monkeypatch.setattr(rakuraku_web, "MAX_WAITING_JOBS", 0)
    fake._job_slots.acquire()
    try:
        started = time.monotonic()
        body = client.post("/seisan/stream", data=FORM).get_data(as_text=True)
    finally:
        fake._job_slots.release()
    assert "ほかの方の処理が続いている" in body and time.monotonic() - started < 2
    assert "event: wait" not in body


def test_password_not_kept_in_memory_after_job(client, fake):
    from rakuraku import redact

    client.post("/seisan/stream", data=dict(FORM, password="very-secret-pw")).get_data(as_text=True)
    assert "very-secret-pw" not in redact._secrets


def test_cancel_flag_stops_long_waits():
    import threading as th

    from rakuraku.errors import JobCancelled
    from rakuraku.locators import set_cancel, wait_for_any

    class _Page:
        def wait_for_timeout(self, ms):
            time.sleep(ms / 1000.0)

    ev = th.Event()
    set_cancel(ev)
    try:
        th.Timer(0.3, ev.set).start()
        started = time.monotonic()
        with pytest.raises(JobCancelled):
            wait_for_any(_Page(), {"never": lambda: False}, timeout_ms=60000)
        assert time.monotonic() - started < 2
    finally:
        set_cancel(None)
