"""Tests for ParkingState: reserve/cancel, capacity, and expiry."""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients"))

from state import ParkingState


# ---------------------------------------------------------------- basics


def _make_state(capacity=5, ttl=300):
    return ParkingState([{"id": "A", "capacity": capacity}], reservation_ttl=ttl)


def test_list_lots():
    s = _make_state(10)
    lots = s.list_lots()
    assert len(lots) == 1
    assert lots[0]["id"] == "A"
    assert lots[0]["capacity"] == 10
    assert lots[0]["free"] == 10


def test_availability():
    s = _make_state(5)
    assert s.availability("A") == 5


def test_reserve_ok():
    s = _make_state(5)
    status, free = s.reserve("A", "P1")
    assert status == "OK"
    assert free == 4


def test_reserve_exists():
    s = _make_state(5)
    s.reserve("A", "P1")
    status, free = s.reserve("A", "P1")
    assert status == "EXISTS"
    assert free == 4


def test_reserve_full():
    s = _make_state(2)
    s.reserve("A", "P1")
    s.reserve("A", "P2")
    status, free = s.reserve("A", "P3")
    assert status == "FULL"
    assert free == 0


def test_cancel_ok():
    s = _make_state(5)
    s.reserve("A", "P1")
    status, free = s.cancel("A", "P1")
    assert status == "OK"
    assert free == 5


def test_cancel_not_found():
    s = _make_state(5)
    status, free = s.cancel("A", "P1")
    assert status == "NOT_FOUND"
    assert free == 5


def test_unknown_lot():
    s = _make_state(5)
    try:
        s.availability("NOPE")
        assert False, "should have raised"
    except ValueError:
        pass


# ---------------------------------------------------------------- sensor


def test_sensor_update():
    s = _make_state(10)
    changed, free = s.apply_sensor_update("A", 3)
    assert changed is True
    assert free == 7


def test_sensor_clamp():
    s = _make_state(5)
    s.apply_sensor_update("A", 100)
    assert s.availability("A") == 0
    s.apply_sensor_update("A", -100)
    assert s.availability("A") == 5


# ---------------------------------------------------------------- expiry


def test_reservation_expiry():
    """Reservations expire after TTL and are purged on next access."""
    s = _make_state(capacity=2, ttl=1)  # TTL=1s
    s.reserve("A", "P1")
    assert s.availability("A") == 1

    # Wait for TTL to elapse
    time.sleep(1.1)

    # After expiry, the spot should be free again
    assert s.availability("A") == 2


def test_no_overbooking_with_expiry():
    """Expired reservations free up space for new ones."""
    s = _make_state(capacity=1, ttl=1)
    s.reserve("A", "P1")

    time.sleep(1.1)

    # P1 expired → should be able to reserve again
    status, free = s.reserve("A", "P2")
    assert status == "OK"
    assert free == 0
