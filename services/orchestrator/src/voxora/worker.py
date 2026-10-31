"""Background worker loop for campaign execution."""

from __future__ import annotations

import logging
import threading

from .campaign import CampaignEngine

logger = logging.getLogger(__name__)


class CampaignWorker(threading.Thread):
    def __init__(self, engine: CampaignEngine, poll_seconds: float = 0.5):
        super().__init__(name="voxora-campaign-worker", daemon=True)
        self.engine = engine
        self.poll_seconds = max(0.05, poll_seconds)
        self._stop = threading.Event()

    def run(self) -> None:
        logger.info("campaign worker started")
        while not self._stop.is_set():
            try:
                processed = self.engine.run_once()
                if processed == 0:
                    self._stop.wait(self.poll_seconds)
            except Exception:  # noqa: BLE001 - the worker must survive bad dials
                logger.exception("worker loop error")
                self._stop.wait(self.poll_seconds)
        logger.info("campaign worker stopped")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self.is_alive():
            self.join(timeout=timeout)
