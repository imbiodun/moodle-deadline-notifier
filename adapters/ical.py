"""Universal iCalendar feed adapter."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

import requests
from icalendar import Calendar


def load_ical(url: str, source: str, timeout: int = 15) -> list[dict[str, Any]]:
    """Fetch an iCalendar feed and convert its events to common records."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return parse_ical(response.content, source=source, url=url)


def parse_ical(content: bytes | str, *, source: str, url: str) -> list[dict[str, Any]]:
    """Parse iCalendar content and return common deadline records."""
    calendar = Calendar.from_ical(content)
    events: list[dict[str, Any]] = []

    for component in calendar.walk("VEVENT"):
        start = component.get("dtstart")
        if start is None:
            continue

        due = start.dt
        if not isinstance(due, (date, datetime)):
            continue

        title = str(component.get("summary", "Untitled"))
        uid = str(component.get("uid", "")).strip()

        events.append(
            {
                "id": uid or _synthetic_id(source, title, due),
                "course": _course(component),
                "title": title,
                "due": due,
                "source": source,
                "url": url,
            }
        )

    return events


def _synthetic_id(source: str, title: str, due: date | datetime) -> str:
    """Stand-in id for feeds that omit UID, derived from the event's own content.

    Without this every UID-less event shares the empty string, so they collapse into
    one another during deduplication and share a single dismiss button.
    """
    fingerprint = f"{source}|{title}|{due.isoformat()}".encode("utf-8")
    return f"ical-{hashlib.sha1(fingerprint).hexdigest()[:16]}"


def _course(component: Any) -> str:
    """Join an event's CATEGORIES, tolerating one or many repeated properties."""
    categories = component.get("categories")
    if categories is None:
        return ""
    # A single CATEGORIES line parses to a vCategory, repeated lines to a list of them,
    # and a bare value may arrive as a plain string.
    groups = categories if isinstance(categories, list) else [categories]
    names: list[str] = []
    for group in groups:
        for category in getattr(group, "cats", [group]):
            name = str(category).strip()
            if name and name not in names:
                names.append(name)
    return ", ".join(names)
