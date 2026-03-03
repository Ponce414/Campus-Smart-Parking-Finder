import threading
import time
from typing import Dict, List, Tuple

RESERVATION_TTL = 300  # 5 minutes default


class ParkingState:
    """
    Thread-safe shared state for parking lots.
    Tracks lot occupancy, reservations (with automatic expiry), and availability.
    Protects all shared data with a single lock for correctness under concurrency.
    """

    def __init__(self, lots: List[dict], reservation_ttl: int = RESERVATION_TTL):
        # Lock protects ALL shared state
        self._lock = threading.Lock()
        self._reservation_ttl = reservation_ttl

        # Internal structure:
        # lot_id -> {capacity, occupied, reservations: {plate: expires_at}}
        self._lots: Dict[str, Dict] = {}

        for lot in lots:
            self._lots[lot["id"]] = {
                "capacity": int(lot["capacity"]),
                "occupied": 0,
                "reservations": {},  # plate -> expires_at timestamp
            }

    def list_lots(self) -> List[dict]:
        with self._lock:
            result = []
            for lot_id, lot in self._lots.items():
                self._purge_expired(lot)
                free = self._compute_free(lot)
                result.append({
                    "id": lot_id,
                    "capacity": lot["capacity"],
                    "occupied": lot["occupied"],
                    "free": free,
                })
            return result

    def availability(self, lot_id: str) -> int:
        with self._lock:
            lot = self._require_lot(lot_id)
            self._purge_expired(lot)
            return self._compute_free(lot)

    def reserve(self, lot_id: str, plate: str) -> Tuple[str, int]:
        """
        Returns:
        ("OK" | "FULL" | "EXISTS", free_after)
        """
        with self._lock:
            lot = self._require_lot(lot_id)
            self._purge_expired(lot)

            if plate in lot["reservations"]:
                return "EXISTS", self._compute_free(lot)

            if self._compute_free(lot) <= 0:
                return "FULL", self._compute_free(lot)

            lot["reservations"][plate] = time.time() + self._reservation_ttl
            return "OK", self._compute_free(lot)

    def cancel(self, lot_id: str, plate: str) -> Tuple[str, int]:
        """
        Returns:
        ("OK" | "NOT_FOUND", free_after)
        """
        with self._lock:
            lot = self._require_lot(lot_id)
            self._purge_expired(lot)

            if plate not in lot["reservations"]:
                return "NOT_FOUND", self._compute_free(lot)

            del lot["reservations"][plate]
            return "OK", self._compute_free(lot)

    def apply_sensor_update(self, lot_id: str, delta: int) -> Tuple[bool, int]:
        """
        Sensor updates occupied count.
        Returns:
        (availability_changed, free_after)
        """
        with self._lock:
            lot = self._require_lot(lot_id)
            self._purge_expired(lot)

            before = self._compute_free(lot)

            lot["occupied"] += int(delta)

            # clamp occupied to [0, capacity]
            lot["occupied"] = max(0, min(lot["capacity"], lot["occupied"]))

            after = self._compute_free(lot)

            return (before != after), after

    def _require_lot(self, lot_id: str) -> Dict:
        if lot_id not in self._lots:
            raise ValueError(f"Unknown lot: {lot_id}")
        return self._lots[lot_id]

    def _compute_free(self, lot: Dict) -> int:
        return max(0, lot["capacity"] - lot["occupied"] - len(lot["reservations"]))

    def _purge_expired(self, lot: Dict) -> None:
        """Remove expired reservations. Must be called while holding the lock."""
        now = time.time()
        expired = [plate for plate, exp in lot["reservations"].items() if exp <= now]
        for plate in expired:
            del lot["reservations"][plate]