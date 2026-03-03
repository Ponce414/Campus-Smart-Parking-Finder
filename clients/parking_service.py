# parking_service.py
"""
Service layer between network servers and state.
Integrates pub/sub for event publishing on state changes.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from state import ParkingState
from pubsub import PubSub

logger = logging.getLogger(__name__)


class ParkingService:
    """
    Layer between network servers and state.
    Publishes events via PubSub whenever a lot's free count changes.
    """

    def __init__(self, state: ParkingState, pubsub: Optional[PubSub] = None):
        self.state = state
        self.pubsub = pubsub

    # ---- internal helper ----

    def _publish_if_changed(self, lot_id: str, status: str, free_after: int,
                            success_status: str = "OK"):
        """Publish an event when *status* equals *success_status* (free changed)."""
        if self.pubsub and status == success_status:
            self.pubsub.publish(lot_id, free_after)

    # -----------------------
    # RPC methods
    # -----------------------

    def rpc_getLots(self) -> List[Dict[str, Any]]:
        return self.state.list_lots()

    def rpc_getAvailability(self, lot_id: str) -> int:
        return self.state.availability(lot_id)

    def rpc_reserve(self, lot_id: str, plate: str) -> bool:
        status, free_after = self.state.reserve(lot_id, plate)
        self._publish_if_changed(lot_id, status, free_after)
        return status == "OK"

    def rpc_cancel(self, lot_id: str, plate: str) -> bool:
        status, free_after = self.state.cancel(lot_id, plate)
        self._publish_if_changed(lot_id, status, free_after)
        return status == "OK"

    def rpc_subscribe(self, lot_id: str) -> int:
        """Subscribe to occupancy changes for a lot.  Returns subId."""
        if not self.pubsub:
            raise RuntimeError("Pub/sub not configured")
        return self.pubsub.subscribe(lot_id)

    def rpc_unsubscribe(self, sub_id: int) -> bool:
        """Unsubscribe.  Returns True if subscription existed."""
        if not self.pubsub:
            raise RuntimeError("Pub/sub not configured")
        return self.pubsub.unsubscribe(int(sub_id))

    # -----------------------
    # Text protocol methods
    # -----------------------

    def text_ping(self) -> str:
        return "PONG"

    def text_lots(self) -> str:
        return json.dumps(self.state.list_lots(), separators=(",", ":"))

    def text_avail(self, lot_id: str) -> str:
        return str(self.state.availability(lot_id))

    def text_reserve(self, lot_id: str, plate: str) -> str:
        status, free_after = self.state.reserve(lot_id, plate)
        self._publish_if_changed(lot_id, status, free_after)
        return status  # OK | FULL | EXISTS

    def text_cancel(self, lot_id: str, plate: str) -> str:
        status, free_after = self.state.cancel(lot_id, plate)
        self._publish_if_changed(lot_id, status, free_after)
        return status  # OK | NOT_FOUND