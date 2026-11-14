"""Load, normalize, filter, and sort assignment deadlines."""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo

from adapters.ical import load_ical
from adapters.manual import load_manual

LOGGER = logging.getLogger(__name__)
HELSINKI = ZoneInfo("Europe/Helsinki")


def load_deadlines(
    sources_path: str | Path,
    courses_path: str | Path,
    ignore_path: str | Path,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load every configured source, returning deadlines and source failures."""
    sources = _read_yaml(sources_path) or []
    course_map = _read_yaml(courses_path) or {}
    ignore = _read_yaml(ignore_path) or {}
    current = _as_helsinki(now or datetime.now(HELSINKI))
    earliest = current - timedelta(days=1)
    config_dir = Path(sources_path).parent
    failures: list[str] = []
    records: dict[tuple[str, str], dict[str, Any]] = {}

    for source_config in sources:
        name = str(source_config.get("name", "unnamed source"))
        # Fetching and normalizing share one guard: a source that breaks part way
        # through should cost us that source, never the rest of the digest.
        try:
            for event in _fetch(source_config, name, config_dir):
                event = _normalize_event(event, course_map)
                if _ignored(event, ignore) or event["due"] < earliest:
                    continue
                records[(event["source"], event["id"])] = event
        except SourceError as exc:
            failures.append(f"{name}: {exc}")
            LOGGER.error("%s: %s", name, exc)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            LOGGER.exception("Unable to load source %s", name)

    return sorted(records.values(), key=lambda event: event["due"]), failures


class SourceError(Exception):
    """A source that cannot be read for a reportable, expected reason."""


def _fetch(
    source_config: dict[str, Any],
    name: str,
    config_dir: Path,
) -> list[dict[str, Any]]:
    """Resolve one source entry to its deadline records."""
    source_type = str(source_config.get("type", "")).strip().casefold()

    if source_type == "manual":
        filename = str(source_config.get("path", "manual.yml"))
        return load_manual(config_dir / filename, source=name)

    if source_type == "ical":
        secret_name = source_config.get("secret")
        url = os.environ.get(str(secret_name), "") if secret_name else ""
        if not url:
            raise SourceError("feed URL is not configured")
        return load_ical(url, source=name)

    raise SourceError(f"unknown source type {source_config.get('type')!r}")


def _read_yaml(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def _normalize_event(event: dict[str, Any], course_map: dict[str, str]) -> dict[str, Any]:
    normalized = dict(event)
    normalized["course"] = _clean_course(str(event.get("course", "")), course_map)
    normalized["due"] = _as_helsinki(event["due"])
    return normalized


def _as_helsinki(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=HELSINKI)
        return value.astimezone(HELSINKI)
    return datetime.combine(value, time.min, tzinfo=HELSINKI)


def _clean_course(course: str, course_map: dict[str, str]) -> str:
    for messy, clean in course_map.items():
        if course == messy:
            return str(clean)
    return course


def _ignored(event: dict[str, Any], ignore: dict[str, Any]) -> bool:
    title = event["title"].casefold()
    for keyword in ignore.get("title_keywords", []):
        if str(keyword).casefold() in title:
            return True
    for pattern in ignore.get("title_patterns", []):
        if re.search(str(pattern), event["title"], flags=re.IGNORECASE):
            return True
    for pattern in ignore.get("title_globs", []):
        if fnmatch.fnmatch(event["title"], str(pattern)):
            return True
    return False
