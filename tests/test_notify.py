from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from notify import build_digest


HELSINKI = ZoneInfo("Europe/Helsinki")
NOW = datetime(2030, 1, 1, 8, tzinfo=HELSINKI)


def event(identifier, title, days):
    return {
        "id": identifier,
        "course": "Course",
        "title": title,
        "due": NOW + timedelta(days=days, hours=4),
    }


def test_digest_groups_deadlines_and_reports_failures():
    digest = build_digest(
        [event("overdue", "Late", -1), event("urgent", "Soon", 2), event("heads", "Later", 5)],
        ["UEF: RequestException"],
        now=NOW,
    )

    assert digest.index("SOURCE FAILURES") < digest.index("OVERDUE")
    assert "DUE WITHIN 3 DAYS (URGENT)" in digest
    assert "DUE IN 4-5 DAYS (HEADS UP)" in digest
    assert "Late" in digest and "Soon" in digest and "Later" in digest


def test_digest_skips_done_and_reports_empty_result():
    digest = build_digest([event("done", "Completed", 1)], now=NOW, done_ids={"done"})

    assert digest == "Nothing due in the next 5 days."
