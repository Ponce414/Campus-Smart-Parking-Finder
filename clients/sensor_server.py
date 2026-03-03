"""
Asynchronous sensor update server.

Sensors connect on a separate TCP port and send newline-delimited commands::

    UPDATE <lotId> <delta>

where ``<delta>`` is ``+1`` or ``-1`` (or any integer).

Architecture
------------
  sensors → TCP Listener → update Queue → worker thread(s) → state + publish

The accept thread reads lines from each sensor connection and pushes
``(lot_id, delta, timestamp)`` tuples into a bounded queue.  A configurable
number of worker threads drain the queue, apply state changes, and publish
events via pub/sub—keeping sensor I/O fully decoupled from RPC and text
request handling.
"""

import logging
import queue
import socket
import threading
import time

from state import ParkingState
from pubsub import PubSub

logger = logging.getLogger(__name__)


class SensorServer:
    """TCP server that ingests sensor updates asynchronously."""

    def __init__(
        self,
        state: ParkingState,
        pubsub: PubSub,
        host: str = "0.0.0.0",
        port: int = 9002,
        num_workers: int = 2,
        queue_size: int = 1024,
    ):
        self.state = state
        self.pubsub = pubsub
        self.host = host
        self.port = port
        self.num_workers = num_workers
        self._update_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._server_sock: socket.socket | None = None

    def start(self) -> list:
        """Start the sensor server + worker threads.  Returns all threads."""
        threads = []

        # Worker threads (drain queue → apply state → publish)
        for i in range(self.num_workers):
            t = threading.Thread(
                target=self._worker, daemon=True, name=f"sensor-worker-{i}"
            )
            t.start()
            threads.append(t)

        # Accept thread
        t = threading.Thread(
            target=self._accept_loop, daemon=True, name="sensor-accept"
        )
        t.start()
        threads.append(t)

        logger.info(
            "SensorServer listening on %s:%d  (workers=%d)",
            self.host,
            self.port,
            self.num_workers,
        )
        return threads

    def stop(self):
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    # ----------------------------------------------------------------

    def _accept_loop(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(32)

        while True:
            try:
                conn, addr = self._server_sock.accept()
            except OSError:
                break
            t = threading.Thread(
                target=self._handle_sensor,
                args=(conn, addr),
                daemon=True,
                name=f"sensor-{addr}",
            )
            t.start()

    def _handle_sensor(self, conn: socket.socket, addr):
        """Read newline-delimited UPDATE commands and enqueue them."""
        logger.info("sensor connected: %s", addr)
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._enqueue_update(line.decode("utf-8").strip(), addr)
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.debug("sensor %s error: %s", addr, e)
        finally:
            logger.info("sensor disconnected: %s", addr)
            try:
                conn.close()
            except OSError:
                pass

    def _enqueue_update(self, line: str, addr):
        """Parse an UPDATE line and put it on the queue."""
        parts = line.split()
        if len(parts) != 3 or parts[0].upper() != "UPDATE":
            logger.warning("sensor %s: bad command: %s", addr, line)
            return

        lot_id = parts[1]
        try:
            delta = int(parts[2])
        except ValueError:
            logger.warning("sensor %s: bad delta: %s", addr, parts[2])
            return

        try:
            self._update_queue.put_nowait((lot_id, delta, time.time()))
        except queue.Full:
            logger.warning(
                "sensor update queue full, dropping: lot=%s delta=%d",
                lot_id,
                delta,
            )

    def _worker(self):
        """Consume updates, apply to state, and publish events on change."""
        while True:
            try:
                lot_id, delta, ts = self._update_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                changed, free_after = self.state.apply_sensor_update(lot_id, delta)
                if changed:
                    self.pubsub.publish(lot_id, free_after)
                logger.info(
                    "sensor update: lot=%s delta=%+d free=%d changed=%s",
                    lot_id,
                    delta,
                    free_after,
                    changed,
                )
            except ValueError as e:
                logger.warning("sensor update error: %s", e)
            except Exception:
                logger.exception("sensor worker error")
