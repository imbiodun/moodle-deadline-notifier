"""Adapter for hand-entered deadlines."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def load_manual(path: str | Path, source: str) -> list[dict[str, Any]]:
    """Read manual deadlines and convert them to common records."""
    with Path(path).open(encoding="utf-8") as file:
        entries = yaml.safe_load(file) or []

    records: list[dict[str, Any]] = []
    for entry in entries:
        course = str(entry["course"])
        title = str(entry["title"])
        due = datetime.fromisoformat(str(entry["due"]))
        records.append(
            {
                "id": _identifier(course, title, due),
                "course": course,
                "title": title,
                "due": due,
                "source": source,
                "url": "",
            }
        )
    return records


def _identifier(course: str, title: str, due: datetime) -> str:
    """Id derived from the entry's content rather than its position in the file.

    Positional ids shift whenever an entry is reordered or removed, which would quietly
    re-point an existing dismissal at a different assignment.
    """
    fingerprint = f"{course}|{title}|{due.isoformat()}".encode("utf-8")
    return f"manual-{hashlib.sha1(fingerprint).hexdigest()[:16]}"
