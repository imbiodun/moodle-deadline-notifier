from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from adapters.ical import parse_ical
from core import load_deadlines

ROOT = Path(__file__).parent
HELSINKI = ZoneInfo("Europe/Helsinki")


def test_parse_ical_returns_common_records():
    events = parse_ical(
        (ROOT / "sample.ics").read_bytes(),
        source="UEF",
        url="https://example.test/private-token",
    )

    assert events[0]["id"] == "essay-1@example.test"
    assert events[0]["course"] == "messy-course"
    assert events[0]["due"].tzinfo is not None
    assert events[1]["due"].isoformat() == "2030-01-03"


def test_core_normalizes_filters_deduplicates_and_sorts(tmp_path, monkeypatch):
    sources = tmp_path / "sources.yml"
    courses = tmp_path / "courses.yml"
    ignore = tmp_path / "ignore.yml"
    sources.write_text(
        " - name: UEF\n   type: ical\n   secret: UEF_URL\n"
        " - name: TUNI\n   type: ical\n   secret: TUNI_URL\n",
        encoding="utf-8",
    )
    courses.write_text("messy-course: Clean Course\n", encoding="utf-8")
    ignore.write_text(
        "title_keywords:\n  - holiday\ntitle_patterns: []\ntitle_globs: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UEF_URL", "https://uef.example/feed")
    monkeypatch.setenv("TUNI_URL", "https://tuni.example/feed")

    def fake_load(url, source):
        if source == "TUNI":
            raise OSError("network down")
        return [
            {
                "id": "a",
                "course": "messy-course",
                "title": "Later",
                "due": datetime(2030, 1, 4, 12, tzinfo=ZoneInfo("UTC")),
                "source": source,
                "url": url,
            },
            {
                "id": "a",
                "course": "messy-course",
                "title": "Duplicate",
                "due": datetime(2030, 1, 4, 12, tzinfo=ZoneInfo("UTC")),
                "source": source,
                "url": url,
            },
        ]

    monkeypatch.setattr("core.load_ical", fake_load)
    events, failures = load_deadlines(
        sources,
        courses,
        ignore,
        now=datetime(2030, 1, 1, tzinfo=HELSINKI),
    )

    assert len(events) == 1
    assert events[0]["course"] == "Clean Course"
    assert events[0]["due"].tzinfo == HELSINKI
    assert failures == ["TUNI: OSError"]
