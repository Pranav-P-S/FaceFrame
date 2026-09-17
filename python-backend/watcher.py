"""Watch mode: a background thread that re-runs the scan pipeline on an
interval so the library keeps up with new files without a manual scan.

The watcher is polite: it never runs concurrently with a manual scan and it
uses the same coalesced event stream, so the UI cannot tell them apart —
which is the point.
"""

import logging
import threading
import time

logger = logging.getLogger("FaceFrame.Watcher")


class Watcher:
    def __init__(self, run_pipeline, interval: float = 30.0):
        self.run_pipeline = run_pipeline  # callable -> stats (or None if busy)
        self.interval = interval
        self.enabled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_enabled(self, enabled: bool, interval: float | None = None):
        if interval:
            self.interval = max(5.0, float(interval))
        self.enabled = bool(enabled)
        if self.enabled and self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="watcher"
            )
            self._thread.start()
        logger.info("Watch mode %s (every %.0fs)",
                    "enabled" if self.enabled else "disabled", self.interval)

    def is_busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._running)

    _running = False

    def _loop(self):
        while not self._stop.is_set():
            if self.enabled:
                self._running = True
                try:
                    self.run_pipeline()
                except Exception:
                    logger.exception("Watch pass failed")
                finally:
                    self._running = False
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
