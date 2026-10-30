"""Universal iCalendar feed adapter."""

from __future__ import annotations

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

        categories = component.get("categories")
        course = ""
        if categories is not None:
            course = ", ".join(str(category).strip() for category in categories.cats)

        events.append(
            {
                "id": str(component.get("uid", "")),
                "course": course,
                "title": str(component.get("summary", "Untitled")),
                "due": due,
                "source": source,
                "url": url,
            }
        )

    return events
