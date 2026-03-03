"""
Multithreaded TCP text-protocol server.

Server organisation choice
--------------------------
**Bounded thread pool** (``concurrent.futures.ThreadPoolExecutor``).

Rationale: A thread pool caps resource usage so that a burst of connections
cannot exhaust OS threads.  The pool size is configurable (default 10 workers),
which is ample for a campus parking system with moderate concurrency.
Compared to thread-per-connection, this avoids unbounded thread creation,
while still giving every connected client its own worker for the duration
of the session.  The listen backlog is set to 64 so that clients arriving
while the pool is fully occupied wait in the kernel queue instead of being
refused immediately.

Protocol (newline-delimited UTF-8)
----------------------------------
  PING                        -> PONG
  LOTS                        -> JSON list of lots
  AVAIL <lotId>               -> integer free
  RESERVE <lotId> <plate>     -> OK | FULL | EXISTS
  CANCEL <lotId> <plate>      -> OK | NOT_FOUND
"""

import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

from parking_service import ParkingService

logger = logging.getLogger(__name__)


class TextServer:
    """TCP text-protocol server backed by a bounded thread pool."""

    def __init__(
        self,
        service: ParkingService,
        host: str = "0.0.0.0",
        port: int = 9000,
        pool_size: int = 10,
    ):
        self.service = service
        self.host = host
        self.port = port
        self.pool_size = pool_size
        self._server_sock: socket.socket | None = None

    def start(self) -> threading.Thread:
        """Start the accept loop in a daemon thread.  Returns the thread."""
        t = threading.Thread(
            target=self._accept_loop, daemon=True, name="text-accept"
        )
        t.start()
        logger.info(
            "TextServer listening on %s:%d  (pool_size=%d)",
            self.host,
            self.port,
            self.pool_size,
        )
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
        self._server_sock.listen(64)

        pool = ThreadPoolExecutor(
            max_workers=self.pool_size, thread_name_prefix="text-worker"
        )

        while True:
            try:
                conn, addr = self._server_sock.accept()
            except OSError:
                break
            pool.submit(self._handle_client, conn, addr)

    def _handle_client(self, conn: socket.socket, addr):
        logger.info("text client connected: %s", addr)
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    response = self._dispatch(line.decode("utf-8").strip())
                    conn.sendall((response + "\n").encode("utf-8"))
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.debug("text client %s error: %s", addr, e)
        finally:
            logger.info("text client disconnected: %s", addr)
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, line: str) -> str:
        """Parse a single text command and return the response string."""
        parts = line.split()
        if not parts:
            return "ERROR empty command"

        cmd = parts[0].upper()

        try:
            if cmd == "PING":
                return self.service.text_ping()
            elif cmd == "LOTS":
                return self.service.text_lots()
            elif cmd == "AVAIL":
                if len(parts) < 2:
                    return "ERROR usage: AVAIL <lotId>"
                return self.service.text_avail(parts[1])
            elif cmd == "RESERVE":
                if len(parts) < 3:
                    return "ERROR usage: RESERVE <lotId> <plate>"
                return self.service.text_reserve(parts[1], parts[2])
            elif cmd == "CANCEL":
                if len(parts) < 3:
                    return "ERROR usage: CANCEL <lotId> <plate>"
                return self.service.text_cancel(parts[1], parts[2])
            else:
                return f"ERROR unknown command: {cmd}"
        except ValueError as e:
            return f"ERROR {e}"
        except Exception:
            logger.exception("dispatch error")
            return "ERROR internal"
