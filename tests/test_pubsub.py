"""Tests for PubSub: subscribe, publish, back-pressure, unsubscribe."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients"))

from pubsub import PubSub


def test_subscribe_returns_unique_ids():
    ps = PubSub()
    s1 = ps.subscribe("A")
    s2 = ps.subscribe("A")
    s3 = ps.subscribe("B")
    assert s1 != s2 != s3


def test_publish_delivers_to_subscriber():
    ps = PubSub()
    sid = ps.subscribe("A")
    ps.publish("A", 10)

    q = ps.get_queue(sid)
    assert q is not None
    event = q.get_nowait()
    assert event.startswith("EVENT A 10 ")


def test_publish_no_crosstalk():
    """Events for lot A should not reach subscribers of lot B."""
    ps = PubSub()
    sid_a = ps.subscribe("A")
    sid_b = ps.subscribe("B")

    ps.publish("A", 5)

    qa = ps.get_queue(sid_a)
    qb = ps.get_queue(sid_b)
    assert not qa.empty()
    assert qb.empty()


def test_unsubscribe():
    ps = PubSub()
    sid = ps.subscribe("A")
    assert ps.unsubscribe(sid) is True
    assert ps.unsubscribe(sid) is False  # already removed
    assert ps.get_queue(sid) is None


def test_publish_after_unsubscribe():
    ps = PubSub()
    sid = ps.subscribe("A")
    ps.unsubscribe(sid)
    # Should not raise
    ps.publish("A", 5)


def test_back_pressure_drop_oldest():
    """When queue is full, oldest event should be dropped."""
    ps = PubSub(max_queue_size=3)
    sid = ps.subscribe("A")

    for i in range(5):
        ps.publish("A", i)

    q = ps.get_queue(sid)
    events = []
    while not q.empty():
        events.append(q.get_nowait())

    # Only the last 3 should remain (oldest 2 dropped)
    assert len(events) == 3
    assert "EVENT A 2 " in events[0]
    assert "EVENT A 3 " in events[1]
    assert "EVENT A 4 " in events[2]


def test_multiple_subscribers_same_lot():
    ps = PubSub()
    s1 = ps.subscribe("A")
    s2 = ps.subscribe("A")

    ps.publish("A", 7)

    q1 = ps.get_queue(s1)
    q2 = ps.get_queue(s2)
    assert not q1.empty()
    assert not q2.empty()

    e1 = q1.get_nowait()
    e2 = q2.get_nowait()
    assert "EVENT A 7 " in e1
    assert "EVENT A 7 " in e2
