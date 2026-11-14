from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from adapters.ical import parse_ical
from adapters.manual import load_manual
from core import load_deadlines

ROOT = Path(__file__).parent
HELSINKI = ZoneInfo("Europe/Helsinki")


def ics(*events: str) -> str:
    body = "".join(events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


def vevent(summary: str, dtstart: str = "20300103", uid: str | None = None) -> str:
    lines = ["BEGIN:VEVENT"]
    if uid is not None:
        lines.append(f"UID:{uid}")
    lines += [f"DTSTART;VALUE=DATE:{dtstart}", f"SUMMARY:{summary}", "END:VEVENT"]
    return "\r\n".join(lines) + "\r\n"


def write_config(tmp_path, sources, courses="{}\n", ignore="title_keywords: []\n"):
    """Write the three config files and return the sources path."""
    (tmp_path / "courses.yml").write_text(courses, encoding="utf-8")
    (tmp_path / "ignore.yml").write_text(ignore, encoding="utf-8")
    (tmp_path / "sources.yml").write_text(sources, encoding="utf-8")
    return tmp_path / "sources.yml"


def run_load(tmp_path, sources, **kwargs):
    sources_path = write_config(tmp_path, sources, **kwargs)
    return load_deadlines(
        sources_path,
        tmp_path / "courses.yml",
        tmp_path / "ignore.yml",
        now=datetime(2030, 1, 1, tzinfo=HELSINKI),
    )


def record(identifier, title, due, source, url=""):
    return {
        "id": identifier,
        "course": "Course",
        "title": title,
        "due": due,
        "source": source,
        "url": url,
    }


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


def test_parse_ical_handles_repeated_categories():
    events = parse_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:multi@example.test\r\n"
        "DTSTART;VALUE=DATE:20300103\r\nSUMMARY:Multi\r\n"
        "CATEGORIES:one\r\nCATEGORIES:two\r\nCATEGORIES:one\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n",
        source="UEF",
        url="https://example.test/feed",
    )

    assert events[0]["course"] == "one, two"


def test_parse_ical_tolerates_missing_categories():
    events = parse_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:bare@example.test\r\n"
        "DTSTART;VALUE=DATE:20300103\r\nSUMMARY:Bare\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n",
        source="UEF",
        url="https://example.test/feed",
    )

    assert events[0]["course"] == ""


def test_parse_ical_gives_uid_less_events_distinct_stable_ids():
    feed = ics(vevent("First"), vevent("Second"), vevent("First", dtstart="20300104"))
    events = parse_ical(feed, source="UEF", url="https://example.test/feed")

    ids = [event["id"] for event in events]
    assert len(set(ids)) == 3, "UID-less events must not collapse into one another"
    assert all(identifier.startswith("ical-") for identifier in ids)

    again = parse_ical(feed, source="UEF", url="https://example.test/feed")
    assert [event["id"] for event in again] == ids, "ids must be stable across runs"


def test_parse_ical_synthetic_ids_do_not_cross_sources():
    feed = ics(vevent("Essay"))
    uef = parse_ical(feed, source="UEF", url="https://uef.example/feed")
    tuni = parse_ical(feed, source="TUNI", url="https://tuni.example/feed")

    assert uef[0]["id"] != tuni[0]["id"]


def test_parse_ical_prefers_a_real_uid_and_ignores_blank_ones():
    events = parse_ical(
        ics(vevent("Real", uid="essay-1@example.test"), vevent("Blank", uid="   ")),
        source="UEF",
        url="https://example.test/feed",
    )

    assert events[0]["id"] == "essay-1@example.test"
    assert events[1]["id"].startswith("ical-")


def test_manual_ids_follow_content_not_file_position(tmp_path):
    first = tmp_path / "first.yml"
    first.write_text(
        "- course: A\n  title: Essay\n  due: 2030-01-02 12:00\n"
        "- course: B\n  title: Lab\n  due: 2030-01-03 12:00\n",
        encoding="utf-8",
    )
    reordered = tmp_path / "reordered.yml"
    reordered.write_text(
        "- course: B\n  title: Lab\n  due: 2030-01-03 12:00\n"
        "- course: A\n  title: Essay\n  due: 2030-01-02 12:00\n",
        encoding="utf-8",
    )

    before = {event["title"]: event["id"] for event in load_manual(first, source="Personal")}
    after = {event["title"]: event["id"] for event in load_manual(reordered, source="Personal")}

    assert before == after, "reordering entries must not reshuffle ids"
    assert len(set(before.values())) == 2


def test_manual_ids_survive_a_removed_entry(tmp_path):
    full = tmp_path / "full.yml"
    full.write_text(
        "- course: A\n  title: Essay\n  due: 2030-01-02 12:00\n"
        "- course: B\n  title: Lab\n  due: 2030-01-03 12:00\n",
        encoding="utf-8",
    )
    trimmed = tmp_path / "trimmed.yml"
    trimmed.write_text("- course: B\n  title: Lab\n  due: 2030-01-03 12:00\n", encoding="utf-8")

    kept = [event for event in load_manual(full, source="Personal") if event["title"] == "Lab"]
    assert kept[0]["id"] == load_manual(trimmed, source="Personal")[0]["id"]


ICAL_SOURCE = " - name: UEF\n   type: ical\n   secret: UEF_URL\n"
TWO_ICAL_SOURCES = ICAL_SOURCE + " - name: TUNI\n   type: ical\n   secret: TUNI_URL\n"


def test_core_normalizes_filters_deduplicates_and_sorts(tmp_path, monkeypatch):
    monkeypatch.setenv("UEF_URL", "https://uef.example/feed")
    monkeypatch.setenv("TUNI_URL", "https://tuni.example/feed")
    utc_noon = datetime(2030, 1, 4, 12, tzinfo=ZoneInfo("UTC"))

    def fake_load(url, source):
        if source == "TUNI":
            raise OSError("network down")
        return [
            record("a", "Later", utc_noon, source, url) | {"course": "messy-course"},
            record("a", "Duplicate", utc_noon, source, url) | {"course": "messy-course"},
        ]

    monkeypatch.setattr("core.load_ical", fake_load)
    events, failures = run_load(
        tmp_path,
        TWO_ICAL_SOURCES,
        courses="messy-course: Clean Course\n",
        ignore="title_keywords:\n  - holiday\ntitle_patterns: []\ntitle_globs: []\n",
    )

    assert len(events) == 1
    assert events[0]["course"] == "Clean Course"
    assert events[0]["due"].tzinfo == HELSINKI
    assert failures == ["TUNI: OSError"]


def test_core_reports_unexpected_adapter_errors_as_source_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("UEF_URL", "https://uef.example/feed")
    monkeypatch.setenv("TUNI_URL", "https://tuni.example/feed")

    def fake_load(url, source):
        if source == "UEF":
            raise AttributeError("'list' object has no attribute 'cats'")
        return [record("b", "Survivor", datetime(2030, 1, 4, 12, tzinfo=HELSINKI), source, url)]

    monkeypatch.setattr("core.load_ical", fake_load)
    events, failures = run_load(tmp_path, TWO_ICAL_SOURCES)

    assert [event["title"] for event in events] == ["Survivor"]
    assert failures == ["UEF: AttributeError"]


def test_core_reports_normalization_errors_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("UEF_URL", "https://uef.example/feed")
    monkeypatch.setattr(
        "core.load_ical",
        lambda url, source: [record("a", "Broken", "not-a-date", source)],
    )

    events, failures = run_load(tmp_path, ICAL_SOURCE)

    assert events == []
    assert failures == ["UEF: TypeError"]


def test_core_keeps_events_read_before_a_bad_one(tmp_path, monkeypatch):
    monkeypatch.setenv("UEF_URL", "https://uef.example/feed")
    monkeypatch.setattr(
        "core.load_ical",
        lambda url, source: [
            record("good", "Fine", datetime(2030, 1, 2, 12, tzinfo=HELSINKI), source),
            record("bad", "Broken", "not-a-date", source),
        ],
    )

    events, failures = run_load(tmp_path, ICAL_SOURCE)

    assert [event["title"] for event in events] == ["Fine"]
    assert failures == ["UEF: TypeError"]


def test_core_reports_unknown_source_types(tmp_path):
    events, failures = run_load(tmp_path, " - name: Typo\n   type: ICAL-feed\n")

    assert events == []
    assert failures == ["Typo: unknown source type 'ICAL-feed'"]


def test_core_accepts_source_types_regardless_of_case(tmp_path, monkeypatch):
    monkeypatch.setenv("UEF_URL", "https://uef.example/feed")
    monkeypatch.setattr(
        "core.load_ical",
        lambda url, source: [
            record("a", "Essay", datetime(2030, 1, 2, 12, tzinfo=HELSINKI), source)
        ],
    )

    events, failures = run_load(tmp_path, " - name: UEF\n   type: iCal\n   secret: UEF_URL\n")

    assert [event["title"] for event in events] == ["Essay"]
    assert failures == []


def test_core_reads_manual_entries_from_a_configured_path(tmp_path):
    (tmp_path / "personal.yml").write_text(
        "- course: A\n  title: Tax return\n  due: 2030-01-03 12:00\n", encoding="utf-8"
    )

    events, failures = run_load(
        tmp_path,
        " - name: Personal\n   type: manual\n   path: personal.yml\n",
    )

    assert [event["title"] for event in events] == ["Tax return"]
    assert failures == []


def test_core_reports_a_missing_manual_file(tmp_path):
    events, failures = run_load(tmp_path, " - name: Personal\n   type: manual\n")

    assert events == []
    assert failures == ["Personal: FileNotFoundError"]
