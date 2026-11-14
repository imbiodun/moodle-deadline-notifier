"""Deadline digest formatting and pluggable notification delivery."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import requests

LOGGER = logging.getLogger(__name__)
DONE_PREFIX = "done:"
KEEP_DONE_FOR = timedelta(days=30)
MESSAGE_LIMIT = 4096  # Telegram's hard cap on sendMessage text
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # seconds before the first retry, doubled each time
TAP_HINT = "✅ Tap to dismiss — it clears on tomorrow's digest, not right away."


class Notifier(Protocol):
    """Interface implemented by notification providers."""

    def send(self, message: str, buttons: list[dict[str, str]] | None = None) -> None:
        """Deliver a message, optionally with dismiss buttons attached."""


class TelegramNotifier:
    """Send messages through the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.bot_token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        if not self.bot_token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    def send(self, message: str, buttons: list[dict[str, str]] | None = None) -> int | None:
        """Deliver the digest, returning the id of the message carrying the buttons."""
        chunks = split_message(message)
        for chunk in chunks[:-1]:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": chunk})

        # Buttons ride on the final chunk so every deadline is already on screen.
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": chunks[-1]}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[button] for button in buttons]}
        sent = self._call("sendMessage", payload)
        return int(sent["message_id"]) if isinstance(sent, dict) and "message_id" in sent else None

    def clear_buttons(self, message_id: int | None) -> None:
        """Strip the dismiss buttons from an earlier digest.

        Yesterday's buttons stay tappable forever otherwise, and because callback data
        is keyed to the deadline rather than the message, tapping a stale one silently
        re-dismisses a deadline that was already restored.
        """
        if not message_id:
            return
        try:
            self._call(
                "editMessageReplyMarkup",
                {"chat_id": self.chat_id, "message_id": message_id},
            )
        except (requests.RequestException, RuntimeError):
            # Telegram refuses edits to messages older than 48h, and the digest is
            # replaced daily anyway, so a failure here costs nothing.
            LOGGER.debug("Could not clear buttons on message %s", message_id)

    def collect_done(self, offset: int = 0) -> tuple[set[str], int]:
        """Drain dismiss taps queued since the last run, with the offset to store next."""
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if offset:
            payload["offset"] = offset
        updates = self._call("getUpdates", payload) or []

        tokens: set[str] = set()
        next_offset = offset
        for update in updates:
            next_offset = max(next_offset, int(update["update_id"]) + 1)
            query = update.get("callback_query")
            if not query:
                continue
            chat = query.get("message", {}).get("chat", {})
            if str(chat.get("id", self.chat_id)) != str(self.chat_id):
                continue
            data = str(query.get("data", ""))
            if data.startswith(DONE_PREFIX):
                tokens.add(data[len(DONE_PREFIX) :])
            self._acknowledge(query.get("id"))
        return tokens, next_offset

    def _acknowledge(self, query_id: str | None) -> None:
        if not query_id:
            return
        try:
            self._call(
                "answerCallbackQuery",
                {"callback_query_id": query_id, "text": "Marked done"},
            )
        except (requests.RequestException, RuntimeError):
            # Telegram drops callback ids after a minute or so, long before the next
            # daily run. The tap itself is already recorded, so this is cosmetic.
            LOGGER.debug("Could not acknowledge callback %s", query_id)

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """Call one Bot API method, retrying the failures that are worth retrying.

        A single dropped connection used to cost the whole digest: the run dies before
        save_state, so the day's reminder never arrives. Retrying a send risks posting
        twice if the reset happened after Telegram accepted the request, which is much
        the lesser problem of the two.
        """
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self._request(method, payload)
            except requests.RequestException as exc:
                if attempt == RETRY_ATTEMPTS or not _retryable(exc):
                    raise
                delay = RETRY_BACKOFF * 2 ** (attempt - 1)
                LOGGER.warning(
                    "%s failed (%s); retrying in %.0fs", method, type(exc).__name__, delay
                )
                time.sleep(delay)
        raise AssertionError("unreachable")  # the loop either returns or raises

    def _request(self, method: str, payload: dict[str, Any]) -> Any:
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/{method}",
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok", False):
            raise RuntimeError(f"Telegram rejected the {method} request")
        return result.get("result")


def _retryable(exc: requests.RequestException) -> bool:
    """Whether a failed Bot API call is worth attempting again.

    Connection resets and timeouts are transient. So are Telegram's own 5xx and its
    429 rate limit. A 400 is us sending something malformed, and no amount of retrying
    will change the answer.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return True
    return response.status_code == 429 or response.status_code >= 500


def split_message(message: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Break a digest into Telegram-sized pieces, splitting on line breaks.

    Telegram rejects anything past 4096 characters outright, so a long digest would
    otherwise fail entirely rather than arrive in parts.
    """
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""
    for line in message.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:  # a single line longer than the limit must still be cut
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def deadline_token(identifier: str) -> str:
    """Short stable handle for a deadline, within Telegram's 64-byte callback limit."""
    return hashlib.sha1(identifier.encode("utf-8")).hexdigest()[:16]


def active_deadlines(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    done_tokens: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Deadlines worth showing today: not dismissed, and due within five days."""
    done = done_tokens or set()
    current = now.date()
    active = [
        event
        for event in events
        if deadline_token(event["id"]) not in done
        and (event["due"].date() - current).days <= 5
    ]
    active.sort(key=lambda event: event["due"])
    return active


def done_buttons(events: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """One dismiss button per shown deadline."""
    return [
        {
            "text": f"✅ {_shorten(str(event['title']))}",
            "callback_data": f"{DONE_PREFIX}{deadline_token(event['id'])}",
        }
        for event in events
    ]


def _shorten(title: str, limit: int = 40) -> str:
    collapsed = " ".join(title.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def build_digest(
    events: list[dict[str, Any]],
    failures: list[str] | None = None,
    *,
    now: datetime,
    done_tokens: set[str] | None = None,
    tap_hint: str = TAP_HINT,
) -> str:
    """Build the morning digest for overdue and five-day deadlines."""
    active = active_deadlines(events, now=now, done_tokens=done_tokens)
    current = now.date()
    sections: list[str] = []

    if failures:
        sections.append("SOURCE FAILURES\n" + "\n".join(f"- {failure}" for failure in failures))

    groups = (
        ("OVERDUE", lambda days: days < 0),
        ("DUE WITHIN 3 DAYS (URGENT)", lambda days: 0 <= days <= 3),
        ("DUE IN 4-5 DAYS (HEADS UP)", lambda days: 4 <= days <= 5),
    )
    listed = False
    for heading, predicate in groups:
        matching = [
            event for event in active if predicate((event["due"].date() - current).days)
        ]
        if matching:
            listed = True
            lines = [heading]
            lines.extend(_format_event(event, current) for event in matching)
            sections.append("\n".join(lines))

    if not listed:
        sections.append("Nothing due in the next 5 days.")
    elif tap_hint:
        # Only needed where a tap is silent until the next run. With the webhook Worker
        # handling taps live, the button answers for itself and the hint is just noise.
        sections.append(tap_hint)
    return "\n\n".join(sections)


def _format_event(event: dict[str, Any], current: Any) -> str:
    course = str(event["course"]).strip()
    label = f"{course}: {event['title']}" if course else str(event["title"])
    days_left = (event["due"].date() - current).days
    return f"- {label} — {event['due']:%a %d %b %H:%M} ({_when(days_left)})"


def _when(days_left: int) -> str:
    """Phrase a day count the way a reminder should read."""
    if days_left < 0:
        return f"{abs(days_left)}d overdue"
    if days_left == 0:
        return "due today"
    if days_left == 1:
        return "due tomorrow"
    return f"in {days_left} days"


def load_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {"done": {}, "offset": 0, "last_sent": "", "last_message_id": 0}
    with state_path.open(encoding="utf-8") as file:
        state = json.load(file)
    return {
        "done": _as_done_map(state.get("done")),
        "offset": int(state.get("offset", 0) or 0),
        "last_sent": str(state.get("last_sent", "")),
        "last_message_id": int(state.get("last_message_id", 0) or 0),
    }


def _as_done_map(done: Any) -> dict[str, dict[str, str]]:
    """Accept the current token map as well as the original list of raw ids."""
    if isinstance(done, dict):
        return {str(token): dict(record) for token, record in done.items()}
    return {
        deadline_token(str(identifier)): {"title": str(identifier), "due": ""}
        for identifier in done or []
    }


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)
        file.write("\n")


def mark_done(
    state: dict[str, Any],
    tokens: set[str],
    events: list[dict[str, Any]],
) -> list[str]:
    """Record dismissals, labelling them from the events we just loaded."""
    by_token = {deadline_token(event["id"]): event for event in events}
    dismissed: list[str] = []
    for token in sorted(tokens):
        event = by_token.get(token)
        state["done"][token] = {
            "title": str(event["title"]) if event else "",
            "due": event["due"].isoformat() if event else "",
        }
        dismissed.append(state["done"][token]["title"] or token)
    return dismissed


def already_sent_today(state: dict[str, Any], *, now: datetime) -> bool:
    """Whether a digest has already gone out on the current local date."""
    return str(state.get("last_sent", "")) == now.date().isoformat()


def record_digest_sent(state: dict[str, Any], *, now: datetime) -> None:
    state["last_sent"] = now.date().isoformat()


def prune_done(state: dict[str, Any], *, now: datetime) -> None:
    """Forget dismissals whose deadline is long past, so state.json stays readable."""
    cutoff = now - KEEP_DONE_FOR
    kept: dict[str, dict[str, str]] = {}
    for token, record in state["done"].items():
        due = str(record.get("due", ""))
        if due:
            try:
                if datetime.fromisoformat(due) < cutoff:
                    continue
            except ValueError:
                LOGGER.debug("Keeping dismissal %s with unreadable due date %r", token, due)
        kept[token] = record
    state["done"] = kept
