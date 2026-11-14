"""Command-line entry point for the assignment reminder."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from core import HELSINKI, load_deadlines
from notify import (
    TelegramNotifier,
    active_deadlines,
    build_digest,
    done_buttons,
    load_state,
    mark_done,
    prune_done,
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    now = datetime.now(HELSINKI)
    events, failures = load_deadlines(args.sources, args.courses, args.ignore, now=now)
    state = load_state(args.state)

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
    save_state(args.state, state)


if __name__ == "__main__":
    main()
