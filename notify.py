"""Deadline digest formatting and pluggable notification delivery."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import requests


class Notifier(Protocol):
    """Interface implemented by notification providers."""

    def send(self, message: str) -> None:
        """Deliver a message."""


class TelegramNotifier:
    """Send messages through the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not self.bot_token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    def send(self, message: str) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": message},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok", False):
            raise RuntimeError("Telegram rejected the message")


def build_digest(
    events: list[dict[str, Any]],
    failures: list[str] | None = None,
    *,
    now: datetime,
    done_ids: set[str] | None = None,
) -> str:
    """Build the morning digest for overdue and five-day deadlines."""
    done = done_ids or set()
    current = now.date()
    active = [
        event
        for event in events
        if event["id"] not in done and (event["due"].date() - current).days <= 5
    ]
    active.sort(key=lambda event: event["due"])
    sections: list[str] = []

    if failures:
        sections.append("SOURCE FAILURES\n" + "\n".join(f"- {failure}" for failure in failures))

    groups = (
        ("OVERDUE", lambda days: days < 0),
        ("DUE WITHIN 3 DAYS (URGENT)", lambda days: 0 <= days <= 3),
        ("DUE IN 4-5 DAYS (HEADS UP)", lambda days: 4 <= days <= 5),
    )
    for heading, predicate in groups:
        matching = [
            event for event in active if predicate((event["due"].date() - current).days)
        ]
        if matching:
            lines = [heading]
            lines.extend(_format_event(event, current) for event in matching)
            sections.append("\n".join(lines))

    if not any(section.splitlines()[0] in {heading for heading, _ in groups} for section in sections):
        sections.append("Nothing due in the next 5 days.")
    return "\n\n".join(sections)


def _format_event(event: dict[str, Any], current: Any) -> str:
    days_left = (event["due"].date() - current).days
    return (
        f"- {event['course']}: {event['title']} — "
        f"{event['due']:%Y-%m-%d %H:%M} ({days_left} days left)"
    )


def load_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {"sent": [], "done": []}
    with state_path.open(encoding="utf-8") as file:
        state = json.load(file)
    return {"sent": state.get("sent", []), "done": state.get("done", [])}


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
        file.write("\n")


def mark_sent(state: dict[str, Any], events: list[dict[str, Any]]) -> None:
    sent = set(state.get("sent", []))
    sent.update(event["id"] for event in events)
    state["sent"] = sorted(sent)
