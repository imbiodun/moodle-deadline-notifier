#!/usr/bin/env python3

import os
import sys
from datetime import date, timedelta

import requests
from icalendar import Calendar


def fail(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def fetch_events(url: str):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    cal = Calendar.from_ical(resp.content)

    events = []
    for component in cal.walk("VEVENT"):
        summary = str(component.get("summary", "Untitled"))
        dtstart = component.get("dtstart")
        if dtstart is None:
            continue
        dt = dtstart.dt
        due_date = dt.date() if hasattr(dt, "date") and callable(getattr(dt, "date")) else dt
        events.append({"title": summary, "due": due_date})
    return events


def build_message(events, days_ahead: int):
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    relevant = [e for e in events if e["due"] <= cutoff]
    relevant.sort(key=lambda e: e["due"])

    if not relevant:
        return None

    lines = []
    for e in relevant:
        days_left = (e["due"] - today).days
        if days_left < 0:
            tag = f"{abs(days_left)}d overdue"
        elif days_left == 0:
            tag = "due today"
        elif days_left == 1:
            tag = "due tomorrow"
        else:
            tag = f"in {days_left}d"
        lines.append(f"- {e['title']} ({tag})")

    return "\n".join(lines)


def send_notification(topic: str, message: str, urgent: bool):
    url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": "Moodle deadlines",
        "Priority": "high" if urgent else "default",
        "Tags": "warning" if urgent else "calendar",
    }
    resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
    resp.raise_for_status()


def main():
    moodle_url = os.environ.get("MOODLE_URL")
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    days_ahead = int(os.environ.get("DAYS_AHEAD", "3"))

    if not moodle_url:
        fail("MOODLE_URL environment variable is not set.")
    if not ntfy_topic:
        fail("NTFY_TOPIC environment variable is not set.")

    events = fetch_events(moodle_url)
    message = build_message(events, days_ahead)

    if message is None:
        send_notification(ntfy_topic, "Nothing due in the next few days. Roll to the other side of the Bed ):!", urgent=False)
        print("Nothing due soon — sent an all-clear notification.")
        return

    has_overdue = "overdue" in message
    send_notification(ntfy_topic, message, urgent=has_overdue)
    print("Notification sent:")
    print(message)


if __name__ == "__main__":
    main()
