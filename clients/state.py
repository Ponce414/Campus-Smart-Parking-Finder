import threading
from typing import Dict, List, Tuple


class ParkingState:
    """
    - Thread-safe shared state
    - Tracks lots
    - Supports availability, reserve, cancel
    """

    def __init__(self, lots: List[dict]):
        # Lock protects ALL shared state
        self._lock = threading.Lock()

        # Internal structure:
        # lot_id -> {capacity, occupied, reservations:set()}
        self._lots: Dict[str, Dict] = {}

        for lot in lots:
            self._lots[lot["id"]] = {
                "capacity": int(lot["capacity"]),
                "occupied": 0,
                "reservations": set(),
            }

    def list_lots(self) -> List[dict]:
        with self._lock:
            result = []
            for lot_id, lot in self._lots.items():
                free = lot["capacity"] - lot["occupied"] - len(lot["reservations"])
                result.append({
                    "id": lot_id,
                    "capacity": lot["capacity"],
                    "occupied": lot["occupied"],
                    "free": max(0, free),
                })
            return result

    def availability(self, lot_id: str) -> int:
        with self._lock:
            lot = self._require_lot(lot_id)
            free = lot["capacity"] - lot["occupied"] - len(lot["reservations"])
            return max(0, free)

    def reserve(self, lot_id: str, plate: str) -> Tuple[str, int]:
        """
        Returns:
        ("OK" | "FULL" | "EXISTS", free_after)
        """
        with self._lock:
            lot = self._require_lot(lot_id)

            if plate in lot["reservations"]:
                return "EXISTS", self._compute_free(lot)

            if self._compute_free(lot) <= 0:
                return "FULL", self._compute_free(lot)

            lot["reservations"].add(plate)
            return "OK", self._compute_free(lot)

    def cancel(self, lot_id: str, plate: str) -> Tuple[str, int]:
        """
        Returns:
        ("OK" | "NOT_FOUND", free_after)
        """
        with self._lock:
            lot = self._require_lot(lot_id)

            if plate not in lot["reservations"]:
                return "NOT_FOUND", self._compute_free(lot)

            lot["reservations"].remove(plate)
            return "OK", self._compute_free(lot)

    def apply_sensor_update(self, lot_id: str, delta: int) -> Tuple[bool, int]:
        """
        Sensor updates occupied count.
        Returns:
        (availability_changed, free_after)
        """
        with self._lock:
            lot = self._require_lot(lot_id)

            before = self._compute_free(lot)

            lot["occupied"] += int(delta)

            # clamp
            lot["occupied"] = max(0, min(lot["capacity"], lot["occupied"]))

            after = self._compute_free(lot)

            return (before != after), after

    def _require_lot(self, lot_id: str) -> Dict:
        if lot_id not in self._lots:
            raise ValueError(f"Unknown lot: {lot_id}")
        return self._lots[lot_id]

    def _compute_free(self, lot: Dict) -> int:
        return max(0, lot["capacity"] - lot["occupied"] - len(lot["reservations"]))