"""Adapter for hand-entered deadlines."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def load_manual(path: str | Path, source: str) -> list[dict[str, Any]]:
    """Read manual deadlines and convert them to common records."""
    with Path(path).open(encoding="utf-8") as file:
        entries = yaml.safe_load(file) or []

    return [
        {
            "id": f"manual-{index}",
            "course": str(entry["course"]),
            "title": str(entry["title"]),
            "due": datetime.fromisoformat(str(entry["due"])),
            "source": source,
            "url": "",
        }
        for index, entry in enumerate(entries, start=1)
    ]
