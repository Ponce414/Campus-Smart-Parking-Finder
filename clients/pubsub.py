"""
Publish/Subscribe system for parking lot occupancy changes.

Back-pressure policy
--------------------
Each subscriber has a bounded ``queue.Queue(maxsize=N)``.
When the queue is full the **oldest event is dropped** to make room
for the newest one (drop-oldest).  Drops are counted and logged so
operators can tune queue sizes or identify slow consumers.

This ensures that publishing never blocks the RPC / sensor worker
threads, keeping the critical path fast.
"""

import logging
import queue
import threading
import time
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class PubSub:
    """
    Manages subscriptions and event fan-out.

    * ``subscribe(lot_id)`` → unique ``sub_id``
    * ``unsubscribe(sub_id)`` → ``bool``
    * ``publish(lot_id, free)`` → fan-out to all subscribers of that lot
    """

    def __init__(self, max_queue_size: int = 64):
        self._lock = threading.Lock()
        self._next_sub_id = 1
        self._max_queue_size = max_queue_size

        # sub_id -> {"lot_id", "queue", "active"}
        self._subs: Dict[int, Dict] = {}
        # lot_id -> set of sub_ids
        self._lot_subs: Dict[str, Set[int]] = {}

    # ---------------------------------------------------------------- API

    def subscribe(self, lot_id: str) -> int:
        """Register a new subscription.  Returns ``sub_id``."""
        with self._lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1

            self._subs[sub_id] = {
                "lot_id": lot_id,
                "queue": queue.Queue(maxsize=self._max_queue_size),
                "active": True,
            }

            if lot_id not in self._lot_subs:
                self._lot_subs[lot_id] = set()
            self._lot_subs[lot_id].add(sub_id)

            logger.info("subscribe  sub_id=%d  lot_id=%s", sub_id, lot_id)
            return sub_id

    def unsubscribe(self, sub_id: int) -> bool:
        """Remove a subscription.  Returns ``True`` if it existed."""
        with self._lock:
            return self._remove_sub(sub_id)

    def publish(self, lot_id: str, free: int) -> None:
        """Fan-out an event to every subscriber of *lot_id*."""
        timestamp = time.time()
        event = f"EVENT {lot_id} {free} {timestamp}"

        with self._lock:
            sub_ids = list(self._lot_subs.get(lot_id, []))

        for sid in sub_ids:
            with self._lock:
                sub = self._subs.get(sid)
                if sub is None or not sub["active"]:
                    continue
                q = sub["queue"]

            # Drop-oldest back-pressure
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()          # discard oldest
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass
                logger.warning(
                    "back-pressure: dropped oldest event for sub_id=%d", sid
                )

    # ---------------------------------------------------------- helpers

    def get_queue(self, sub_id: int) -> Optional[queue.Queue]:
        """Return the event queue for *sub_id* (used by the notifier)."""
        with self._lock:
            sub = self._subs.get(sub_id)
            if sub and sub["active"]:
                return sub["queue"]
            return None

    def is_active(self, sub_id: int) -> bool:
        with self._lock:
            sub = self._subs.get(sub_id)
            return sub is not None and sub["active"]

    # ---------------------------------------------------------- internal

    def _remove_sub(self, sub_id: int) -> bool:
        """Remove *sub_id* (caller must hold ``_lock``).  Returns ``True`` if found."""
        sub = self._subs.pop(sub_id, None)
        if sub is None:
            return False
        lot_id = sub["lot_id"]
        sub["active"] = False
        if lot_id in self._lot_subs:
            self._lot_subs[lot_id].discard(sub_id)
            if not self._lot_subs[lot_id]:
                del self._lot_subs[lot_id]
        logger.info("unsubscribe  sub_id=%d  lot_id=%s", sub_id, lot_id)
        return True
