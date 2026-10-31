"""Command-line entry point for the assignment reminder."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from core import HELSINKI, load_deadlines
from notify import CallMeBotNotifier, build_digest, load_state, mark_sent, save_state


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
    digest = build_digest(events, failures, now=now, done_ids=set(state["done"]))

    if args.dry_run:
        print(digest)
        return

    CallMeBotNotifier().send(digest)
    mark_sent(state, events)
    save_state(args.state, state)


if __name__ == "__main__":
    main()
