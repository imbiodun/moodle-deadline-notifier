import pytest
import requests

from dismissals import WorkerDismissals, dismissal_source


class Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self._payload


def test_requires_both_url_and_secret(monkeypatch):
    monkeypatch.delenv("DISMISS_API_URL", raising=False)
    monkeypatch.delenv("DISMISS_API_SECRET", raising=False)

    with pytest.raises(ValueError):
        WorkerDismissals()

    # A half-configured Worker is a mistake worth failing on, not a reason to fall back.
    monkeypatch.setenv("DISMISS_API_URL", "https://worker.example.test")
    with pytest.raises(ValueError):
        WorkerDismissals()


def test_dismissal_source_falls_back_to_polling_when_unset(monkeypatch):
    monkeypatch.delenv("DISMISS_API_URL", raising=False)
    monkeypatch.delenv("DISMISS_API_SECRET", raising=False)

    assert dismissal_source() is None


def test_collect_returns_pending_tokens(monkeypatch):
    called = {}

    def fake_get(url, *, headers, timeout):
        called.update(url=url, headers=headers)
        return Response(
            {"dismissals": [{"token": "aaa", "title": "Essay"}, {"token": "bbb", "title": ""}]}
        )

    monkeypatch.setattr("dismissals.requests.get", fake_get)
    queue = WorkerDismissals("https://worker.example.test/", "s3cret")

    assert queue.collect() == {"aaa", "bbb"}
    assert called["url"] == "https://worker.example.test/dismissals"
    assert called["headers"] == {"Authorization": "Bearer s3cret"}


def test_acknowledge_drains_only_what_was_collected(monkeypatch):
    posted = {}

    def fake_post(url, *, headers, json, timeout):
        posted.update(url=url, json=json)
        return Response({"ok": True, "acked": len(json["tokens"])})

    monkeypatch.setattr("dismissals.requests.post", fake_post)
    queue = WorkerDismissals("https://worker.example.test", "s3cret")
    queue.acknowledge({"bbb", "aaa"})

    assert posted["url"] == "https://worker.example.test/dismissals/ack"
    assert posted["json"] == {"tokens": ["aaa", "bbb"]}


def test_trailing_whitespace_in_a_secret_does_not_break_the_header(monkeypatch):
    called = {}

    def fake_get(url, *, headers, timeout):
        called.update(url=url, headers=headers)
        return Response({"dismissals": []})

    monkeypatch.setattr("dismissals.requests.get", fake_get)
    # What `gh secret set DISMISS_API_SECRET < secret.txt` stores.
    queue = WorkerDismissals("https://worker.example.test\n", "s3cret\n")
    queue.collect()

    assert called["url"] == "https://worker.example.test/dismissals"
    assert called["headers"] == {"Authorization": "Bearer s3cret"}


def test_acknowledge_skips_the_call_when_nothing_was_tapped(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("should not call the Worker with an empty token set")

    monkeypatch.setattr("dismissals.requests.post", fail)
    WorkerDismissals("https://worker.example.test", "s3cret").acknowledge(set())
