"""
Event notification server (pub/sub delivery).

Subscribers:
  1. Call ``subscribe(lotId)`` via the RPC server to obtain a ``subId``.
  2. Open a TCP connection to the **event port** (default 9003).
  3. Send a single line: ``SUB <subId>\\n``
  4. Receive ``OK\\n`` on success (or ``ERROR …\\n``).
  5. Receive newline-delimited events:  ``EVENT <lotId> <free> <timestamp>\\n``
  6. On disconnect, the subscription is automatically removed.

Each subscriber connection is served by a dedicated daemon thread that
drains the subscriber's bounded queue and writes to the socket.
"""

import logging
import socket
import threading

from pubsub import PubSub

logger = logging.getLogger(__name__)


class NotifierServer:
    """TCP server for event delivery to subscribers."""

    def __init__(self, pubsub: PubSub, host: str = "0.0.0.0", port: int = 9003):
        self.pubsub = pubsub
        self.host = host
        self.port = port
        self._server_sock: socket.socket | None = None

    def start(self) -> threading.Thread:
        """Start the notifier accept loop in a daemon thread."""
        t = threading.Thread(
            target=self._accept_loop, daemon=True, name="notifier-accept"
        )
        t.start()
        logger.info("NotifierServer listening on %s:%d", self.host, self.port)
        return t

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
                target=self._handle_subscriber,
                args=(conn, addr),
                daemon=True,
                name=f"notifier-{addr}",
            )
            t.start()

    def _handle_subscriber(self, conn: socket.socket, addr):
        """
        Handshake: read ``SUB <subId>\\n``, then push events until disconnect.
        """
        sub_id = None
        try:
            conn.settimeout(30.0)  # timeout for initial SUB line
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(1024)
                if not chunk:
                    return
                buf += chunk

            line = buf[: buf.index(b"\n")].decode("utf-8").strip()
            parts = line.split()

            if len(parts) != 2 or parts[0].upper() != "SUB":
                conn.sendall(b"ERROR bad handshake\n")
                return

            try:
                sub_id = int(parts[1])
            except ValueError:
                conn.sendall(b"ERROR invalid subId\n")
                return

            q = self.pubsub.get_queue(sub_id)
            if q is None:
                conn.sendall(b"ERROR unknown or inactive subId\n")
                return

            conn.settimeout(None)   # blocking mode for delivery
            conn.sendall(b"OK\n")
            logger.info(
                "notifier: subscriber sub_id=%d connected from %s", sub_id, addr
            )

            # ---------- delivery loop
            while self.pubsub.is_active(sub_id):
                try:
                    event = q.get(timeout=5.0)
                except Exception:
                    continue  # timeout – just re-check active flag

                try:
                    conn.sendall((event + "\n").encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logger.info(
                        "notifier: subscriber sub_id=%d disconnected", sub_id
                    )
                    break

        except Exception as e:
            logger.debug("notifier handler error: %s", e)
        finally:
            if sub_id is not None:
                self.pubsub.unsubscribe(sub_id)
            try:
                conn.close()
            except OSError:
                pass
