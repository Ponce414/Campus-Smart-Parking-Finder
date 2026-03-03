#!/usr/bin/env python3
"""
Campus Smart Parking Finder – Main Server Entry Point

Starts all server components sharing a single ParkingState and PubSub:
  - Text protocol server  (default port 9000)
  - RPC server            (default port 9001)
  - Sensor server         (default port 9002)
  - Event notifier server (default port 9003)

Configuration is read from ``config.json`` in the same directory (if present),
falling back to sensible defaults.
"""

import json
import logging
import os
import time

from state import ParkingState
from pubsub import PubSub
from parking_service import ParkingService
from text_server import TextServer
from rpc_server import RpcServer
from sensor_server import SensorServer
from notifier import NotifierServer

# ---------------------------------------------------------------- defaults

DEFAULT_CONFIG = {
    "lots": [
        {"id": "LOT-A", "capacity": 100},
        {"id": "LOT-B", "capacity": 200},
        {"id": "LOT-C", "capacity": 50},
    ],
    "text_port": 9000,
    "rpc_port": 9001,
    "sensor_port": 9002,
    "event_port": 9003,
    "pool_size": 10,
    "sensor_workers": 2,
    "reservation_ttl": 300,
    "pubsub_queue_size": 64,
}


def load_config() -> dict:
    """Merge ``config.json`` (if found) over defaults."""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            user = json.load(f)
        return {**DEFAULT_CONFIG, **user}
    return dict(DEFAULT_CONFIG)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(threadName)-18s] %(levelname)-5s %(name)s – %(message)s",
    )
    logger = logging.getLogger("main")

    config = load_config()
    logger.info("Configuration:\n%s", json.dumps(config, indent=2))

    # ---- shared objects ----
    state = ParkingState(config["lots"], reservation_ttl=config["reservation_ttl"])
    pubsub = PubSub(max_queue_size=config["pubsub_queue_size"])
    service = ParkingService(state, pubsub)

    # ---- start servers ----
    text_srv = TextServer(service, port=config["text_port"], pool_size=config["pool_size"])
    rpc_srv = RpcServer(service, port=config["rpc_port"], pool_size=config["pool_size"])
    sensor_srv = SensorServer(
        state, pubsub,
        port=config["sensor_port"],
        num_workers=config["sensor_workers"],
    )
    notifier_srv = NotifierServer(pubsub, port=config["event_port"])

    text_srv.start()
    rpc_srv.start()
    sensor_srv.start()
    notifier_srv.start()

    logger.info("=" * 60)
    logger.info("All servers started:")
    logger.info("  Text server      →  port %d", config["text_port"])
    logger.info("  RPC server       →  port %d", config["rpc_port"])
    logger.info("  Sensor server    →  port %d", config["sensor_port"])
    logger.info("  Event notifier   →  port %d", config["event_port"])
    logger.info("  Lots: %s", [l["id"] for l in config["lots"]])
    logger.info("  Reservation TTL: %ds", config["reservation_ttl"])
    logger.info("=" * 60)

    # ---- block until Ctrl-C ----
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down …")
        text_srv.stop()
        rpc_srv.stop()
        sensor_srv.stop()
        notifier_srv.stop()
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
