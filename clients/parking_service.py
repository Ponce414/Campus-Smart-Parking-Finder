# parking_service.py
from typing import Any, Dict, List
from state import ParkingState


class ParkingService:
    """
    - Layer between network servers and state
    """

    def __init__(self, state: ParkingState):
        self.state = state

    # -----------------------
    # RPC methods
    # -----------------------

    def rpc_getLots(self) -> List[Dict[str, Any]]:
        return self.state.list_lots()

    def rpc_getAvailability(self, lot_id: str) -> int:
        return self.state.availability(lot_id)

    def rpc_reserve(self, lot_id: str, plate: str) -> bool:
        status, _ = self.state.reserve(lot_id, plate)
        return status == "OK"

    def rpc_cancel(self, lot_id: str, plate: str) -> bool:
        status, _ = self.state.cancel(lot_id, plate)
        return status == "OK"

    # -----------------------
    # Text protocol methods
    # -----------------------

    def text_ping(self) -> str:
        return "PONG"

    def text_lots(self) -> str:
        import json
        return json.dumps(self.state.list_lots(), separators=(",", ":"))

    def text_avail(self, lot_id: str) -> str:
        return str(self.state.availability(lot_id))

    def text_reserve(self, lot_id: str, plate: str) -> str:
        status, _ = self.state.reserve(lot_id, plate)
        return status  # OK | FULL | EXISTS

    def text_cancel(self, lot_id: str, plate: str) -> str:
        status, _ = self.state.cancel(lot_id, plate)
        return status  # OK | NOT_FOUND