"""Dismissals collected by the Telegram webhook Worker.

Setting a webhook disables getUpdates - Telegram answers it with 409 Conflict - so the
two collection paths are mutually exclusive. The daily job picks whichever is configured
and TelegramNotifier.collect_done stays as the fallback for a repo with no Worker.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)


class WorkerDismissals:
    """Pending dismiss taps parked in the Worker's KV queue."""

    def __init__(self, base_url: str | None = None, secret: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("DISMISS_API_URL", "")).rstrip("/")
        self.secret = secret or os.environ.get("DISMISS_API_SECRET", "")
        if not self.base_url or not self.secret:
            raise ValueError("DISMISS_API_URL and DISMISS_API_SECRET are required")

    def collect(self) -> set[str]:
        """Every tap recorded since the last acknowledge."""
        response = requests.get(
            f"{self.base_url}/dismissals",
            headers={"Authorization": f"Bearer {self.secret}"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        return {str(entry["token"]) for entry in payload.get("dismissals", [])}

    def acknowledge(self, tokens: set[str]) -> None:
        """Drain the queue, once the tokens are safely written to state.

        Called after save_state deliberately. A run that dies mid-digest leaves the taps
        in KV to be collected again, rather than dropping them on the floor.
        """
        if not tokens:
            return
        response = requests.post(
            f"{self.base_url}/dismissals/ack",
            headers={"Authorization": f"Bearer {self.secret}"},
            json={"tokens": sorted(tokens)},
            timeout=15,
        )
        response.raise_for_status()


def dismissal_source() -> WorkerDismissals | None:
    """The Worker queue when it is configured, otherwise None for getUpdates polling."""
    try:
        return WorkerDismissals()
    except ValueError:
        LOGGER.debug("No dismissal Worker configured; falling back to getUpdates")
        return None
