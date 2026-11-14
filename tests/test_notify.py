import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests

from notify import (
    RETRY_ATTEMPTS,
    TAP_HINT,
    TelegramNotifier,
    already_sent_today,
    build_digest,
    deadline_token,
    done_buttons,
    load_state,
    mark_done,
    prune_done,
    record_digest_sent,
    save_state,
    split_message,
)


HELSINKI = ZoneInfo("Europe/Helsinki")
NOW = datetime(2030, 1, 1, 8, tzinfo=HELSINKI)


def event(identifier, title, days):
    return {
        "id": identifier,
        "course": "Course",
        "title": title,
        "due": NOW + timedelta(days=days, hours=4),
    }


class Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_digest_groups_deadlines_and_reports_failures():
    digest = build_digest(
        [event("overdue", "Late", -1), event("urgent", "Soon", 2), event("heads", "Later", 5)],
        ["UEF: RequestException"],
        now=NOW,
    )

    assert digest.index("SOURCE FAILURES") < digest.index("OVERDUE")
    assert "DUE WITHIN 3 DAYS (URGENT)" in digest
    assert "DUE IN 4-5 DAYS (HEADS UP)" in digest
    assert "Late" in digest and "Soon" in digest and "Later" in digest


def test_digest_skips_done_and_reports_empty_result():
    digest = build_digest(
        [event("done", "Completed", 1)],
        now=NOW,
        done_tokens={deadline_token("done")},
    )

    assert digest == "Nothing due in the next 5 days."


def test_digest_reports_failures_even_when_nothing_is_due():
    digest = build_digest([], ["UEF: RequestException"], now=NOW)

    assert digest.startswith("SOURCE FAILURES")
    assert digest.endswith("Nothing due in the next 5 days.")


def test_telegram_notifier_sends_message(monkeypatch):
    sent = {}

    def fake_post(url, *, json, timeout):
        sent.update(url=url, json=json, timeout=timeout)
        return Response({"ok": True})

    monkeypatch.setattr("notify.requests.post", fake_post)
    TelegramNotifier("token", "chat").send("digest")

    assert sent == {
        "url": "https://api.telegram.org/bottoken/sendMessage",
        "json": {"chat_id": "chat", "text": "digest"},
        "timeout": 15,
    }


def test_telegram_notifier_attaches_dismiss_buttons(monkeypatch):
    sent = {}

    def fake_post(url, *, json, timeout):
        sent.update(json=json)
        return Response({"ok": True})

    monkeypatch.setattr("notify.requests.post", fake_post)
    buttons = done_buttons([event("essay-1@uef", "Essay submission", 1)])
    TelegramNotifier("token", "chat").send("digest", buttons)

    assert sent["json"]["reply_markup"] == {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Essay submission",
                    "callback_data": f"done:{deadline_token('essay-1@uef')}",
                }
            ]
        ]
    }


def test_done_button_callback_data_fits_telegram_limit():
    long_id = "a" * 500 + "@moodle.example.test"
    button = done_buttons([event(long_id, "x" * 200, 1)])[0]

    assert len(button["callback_data"].encode("utf-8")) <= 64
    assert len(button["text"]) <= 42


def test_collect_done_returns_tapped_tokens_and_next_offset(monkeypatch):
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append((url.rsplit("/", 1)[-1], json))
        if url.endswith("/getUpdates"):
            return Response(
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 41,
                            "callback_query": {
                                "id": "q1",
                                "data": "done:abc123",
                                "message": {"chat": {"id": "chat"}},
                            },
                        },
                        {
                            "update_id": 42,
                            "callback_query": {
                                "id": "q2",
                                "data": "done:ignored",
                                "message": {"chat": {"id": "someone-else"}},
                            },
                        },
                    ],
                }
            )
        return Response({"ok": True})

    monkeypatch.setattr("notify.requests.post", fake_post)
    tokens, offset = TelegramNotifier("token", "chat").collect_done(7)

    assert tokens == {"abc123"}
    assert offset == 43
    assert calls[0] == (
        "getUpdates",
        {"timeout": 0, "allowed_updates": ["callback_query"], "offset": 7},
    )
    assert "answerCallbackQuery" in [method for method, _ in calls]


def test_collect_done_survives_stale_acknowledgement(monkeypatch):
    def fake_post(url, *, json, timeout):
        if url.endswith("/getUpdates"):
            return Response(
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 5,
                            "callback_query": {
                                "id": "q1",
                                "data": "done:abc123",
                                "message": {"chat": {"id": "chat"}},
                            },
                        }
                    ],
                }
            )
        return Response({"ok": False, "description": "query is too old"})

    monkeypatch.setattr("notify.requests.post", fake_post)
    tokens, offset = TelegramNotifier("token", "chat").collect_done(0)

    assert tokens == {"abc123"}
    assert offset == 6


def test_mark_done_labels_dismissals_from_loaded_events():
    state = {"done": {}, "offset": 0}
    events = [event("essay-1@uef", "Essay submission", 1)]
    token = deadline_token("essay-1@uef")

    dismissed = mark_done(state, {token, "unknown"}, events)

    assert state["done"][token]["title"] == "Essay submission"
    assert state["done"][token]["due"].startswith("2030-01-02")
    assert state["done"]["unknown"] == {"title": "", "due": ""}
    assert "Essay submission" in dismissed


def test_prune_done_drops_long_past_dismissals():
    state = {
        "done": {
            "old": {"title": "Ancient", "due": "2029-10-01T12:00:00+03:00"},
            "recent": {"title": "Fresh", "due": "2029-12-28T12:00:00+02:00"},
            "unknown": {"title": "Migrated", "due": ""},
        }
    }

    prune_done(state, now=NOW)

    assert set(state["done"]) == {"recent", "unknown"}


def test_state_round_trip_and_legacy_migration(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"sent": ["a", "b"], "done": ["essay-1@uef"]}), encoding="utf-8")

    state = load_state(path)

    assert "sent" not in state
    assert state["offset"] == 0
    assert deadline_token("essay-1@uef") in state["done"]

    state["offset"] = 99
    save_state(path, state)

    assert load_state(path)["offset"] == 99


def test_load_state_defaults_when_missing(tmp_path):
    assert load_state(tmp_path / "absent.json") == {
        "done": {},
        "offset": 0,
        "last_sent": "",
        "last_message_id": 0,
    }


def test_digest_phrases_day_counts_for_a_human():
    digest = build_digest(
        [
            event("late", "Late", -2),
            event("today", "Today", 0),
            event("tomorrow", "Tomorrow", 1),
            event("later", "Later", 4),
        ],
        now=NOW,
    )

    assert "(2d overdue)" in digest
    assert "(due today)" in digest
    assert "(due tomorrow)" in digest
    assert "(in 4 days)" in digest
    assert "days left" not in digest


def test_digest_omits_an_empty_course_prefix():
    uncategorised = event("bare", "Essay submission", 1)
    uncategorised["course"] = "  "

    digest = build_digest([uncategorised], now=NOW)

    assert "- Essay submission —" in digest
    assert ": Essay submission" not in digest


def test_split_message_leaves_short_digests_alone():
    assert split_message("short") == ["short"]


def test_split_message_breaks_on_line_boundaries():
    lines = [f"- Course: Assignment number {index}" for index in range(400)]
    message = "\n".join(lines)
    chunks = split_message(message)

    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "\n".join(chunks) == message, "splitting must not lose or alter any line"


def test_split_message_cuts_a_single_oversized_line():
    chunks = split_message("x" * 9000)

    assert [len(chunk) for chunk in chunks] == [4096, 4096, 808]
    assert "".join(chunks) == "x" * 9000


def test_telegram_notifier_splits_long_digests_and_buttons_ride_the_last(monkeypatch):
    sent = []

    def fake_post(url, *, json, timeout):
        sent.append(json)
        return Response({"ok": True})

    monkeypatch.setattr("notify.requests.post", fake_post)
    message = "\n".join(f"- Course: Assignment number {index}" for index in range(400))
    buttons = done_buttons([event("essay-1@uef", "Essay submission", 1)])
    TelegramNotifier("token", "chat").send(message, buttons)

    assert len(sent) > 1
    assert "reply_markup" not in sent[0]
    assert "reply_markup" in sent[-1]
    assert "\n".join(payload["text"] for payload in sent) == message


def test_already_sent_today_tracks_the_local_date():
    state = {"done": {}, "offset": 0, "last_sent": ""}
    assert not already_sent_today(state, now=NOW)

    record_digest_sent(state, now=NOW)
    assert state["last_sent"] == "2030-01-01"
    assert already_sent_today(state, now=NOW)

    assert not already_sent_today(state, now=NOW + timedelta(days=1))


def test_last_sent_survives_a_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = {"done": {}, "offset": 0, "last_sent": ""}
    record_digest_sent(state, now=NOW)
    save_state(path, state)

    assert already_sent_today(load_state(path), now=NOW)


def test_digest_explains_that_taps_land_on_the_next_run():
    listed = build_digest([event("urgent", "Soon", 2)], now=NOW)
    assert listed.endswith(TAP_HINT)

    # With no buttons to tap, the hint would just be noise.
    assert TAP_HINT not in build_digest([], now=NOW)

    # Nor is it wanted once the webhook Worker answers taps on the spot.
    assert TAP_HINT not in build_digest([event("urgent", "Soon", 2)], now=NOW, tap_hint="")


def test_send_returns_the_message_id_carrying_the_buttons(monkeypatch):
    monkeypatch.setattr(
        "notify.requests.post",
        lambda url, *, json, timeout: Response({"ok": True, "result": {"message_id": 77}}),
    )

    assert TelegramNotifier("token", "chat").send("digest") == 77


def test_clear_buttons_edits_the_previous_digest(monkeypatch):
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append((url, json))
        return Response({"ok": True})

    monkeypatch.setattr("notify.requests.post", fake_post)
    notifier = TelegramNotifier("token", "chat")
    notifier.clear_buttons(77)

    assert calls == [
        (
            "https://api.telegram.org/bottoken/editMessageReplyMarkup",
            {"chat_id": "chat", "message_id": 77},
        )
    ]

    # No previous digest on the very first run, so there is nothing to strip.
    notifier.clear_buttons(0)
    assert len(calls) == 1


def test_clear_buttons_survives_an_uneditable_message(monkeypatch):
    def fake_post(url, *, json, timeout):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr("notify.requests.post", fake_post)
    monkeypatch.setattr("notify.time.sleep", lambda seconds: None)

    TelegramNotifier("token", "chat").clear_buttons(77)  # must not raise


def test_call_retries_a_dropped_connection(monkeypatch):
    attempts = []
    slept = []

    def fake_post(url, *, json, timeout):
        attempts.append(json)
        if len(attempts) < 3:
            raise requests.ConnectionError("Connection reset by peer")
        return Response({"ok": True, "result": {"message_id": 5}})

    monkeypatch.setattr("notify.requests.post", fake_post)
    monkeypatch.setattr("notify.time.sleep", slept.append)

    assert TelegramNotifier("token", "chat").send("digest") == 5
    assert len(attempts) == 3
    assert slept == [2.0, 4.0]  # backoff doubles between attempts


def test_call_gives_up_after_the_last_attempt(monkeypatch):
    attempts = []

    def fake_post(url, *, json, timeout):
        attempts.append(json)
        raise requests.ConnectionError("Connection reset by peer")

    monkeypatch.setattr("notify.requests.post", fake_post)
    monkeypatch.setattr("notify.time.sleep", lambda seconds: None)

    with pytest.raises(requests.ConnectionError):
        TelegramNotifier("token", "chat").send("digest")
    assert len(attempts) == RETRY_ATTEMPTS


def test_call_does_not_retry_a_rejected_request(monkeypatch):
    attempts = []

    def fake_post(url, *, json, timeout):
        attempts.append(json)
        error = requests.HTTPError("400 Bad Request")
        error.response = Response({"ok": False})
        error.response.status_code = 400
        raise error

    monkeypatch.setattr("notify.requests.post", fake_post)
    monkeypatch.setattr("notify.time.sleep", lambda seconds: None)

    # A malformed request fails the same way every time; retrying only delays the run.
    with pytest.raises(requests.HTTPError):
        TelegramNotifier("token", "chat").send("digest")
    assert len(attempts) == 1
