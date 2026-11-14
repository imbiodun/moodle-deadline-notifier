"""Command-line entry point for the assignment reminder."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from core import HELSINKI, load_deadlines
from notify import (
    TelegramNotifier,
    active_deadlines,
    already_sent_today,
    build_digest,
    done_buttons,
    load_state,
    mark_done,
    prune_done,
    record_digest_sent,
    save_state,
)

LOGGER = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the digest without sending")
    parser.add_argument("--sources", default="config/sources.yml")
    parser.add_argument("--courses", default="config/courses.yml")
    parser.add_argument("--ignore", default="config/ignore.yml")
    parser.add_argument("--state", default="state.json")
    parser.add_argument(
        "--once-per-day",
        action="store_true",
        help="exit without sending if a digest already went out today",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    now = datetime.now(HELSINKI)
    state = load_state(args.state)

    # Checked before fetching anything: the fallback schedule runs hours late, by
    # which point the prompt run has usually already delivered the day's digest.
    if args.once_per_day and already_sent_today(state, now=now):
        LOGGER.info("A digest already went out on %s; nothing to do.", now.date())
        return

    events, failures = load_deadlines(args.sources, args.courses, args.ignore, now=now)

    if args.dry_run:
        print(build_digest(events, failures, now=now, done_tokens=set(state["done"])))
        return

    notifier = TelegramNotifier()

    # Nothing listens between daily runs, so collect yesterday's dismiss taps first.
    tokens, state["offset"] = notifier.collect_done(state["offset"])
    dismissed = mark_done(state, tokens, events)
    if dismissed:
        LOGGER.info("Dismissed since the last run: %s", ", ".join(dismissed))
    prune_done(state, now=now)

    done_tokens = set(state["done"])
    digest = build_digest(events, failures, now=now, done_tokens=done_tokens)
    shown = active_deadlines(events, now=now, done_tokens=done_tokens)
    notifier.send(digest, done_buttons(shown))
    record_digest_sent(state, now=now)
    save_state(args.state, state)


if __name__ == "__main__":
    main()
