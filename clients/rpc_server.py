"""
RPC server – length-prefixed JSON over TCP.

Uses a bounded thread pool (same rationale as the text server) for
concurrent connection handling.  Each connection can issue multiple
sequential RPC requests; the server reads frames in a loop until EOF.

Dispatch
--------
The server skeleton maps ``method`` in the request to
``ParkingService.rpc_<method>`` and invokes it with ``*args``.
Unknown methods and exceptions are translated into error replies.
"""

import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

from parking_service import ParkingService
from rpc_protocol import RpcProtocol

logger = logging.getLogger(__name__)


class RpcServer:
    """RPC server backed by a bounded thread pool."""

    def __init__(
        self,
        service: ParkingService,
        host: str = "0.0.0.0",
        port: int = 9001,
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
            target=self._accept_loop, daemon=True, name="rpc-accept"
        )
        t.start()
        logger.info(
            "RpcServer listening on %s:%d  (pool_size=%d)",
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
            max_workers=self.pool_size, thread_name_prefix="rpc-worker"
        )

        while True:
            try:
                conn, addr = self._server_sock.accept()
            except OSError:
                break
            pool.submit(self._handle_client, conn, addr)

    def _handle_client(self, conn: socket.socket, addr):
        """Read frames in a loop, dispatch each, and reply."""
        logger.info("rpc client connected: %s", addr)
        try:
            while True:
                request = RpcProtocol.decode_from_socket(conn)
                if request is None:
                    break  # clean EOF

                rpc_id = request.get("rpcId", 0)
                method = request.get("method", "")
                args = request.get("args", [])

                reply = self._dispatch(rpc_id, method, args)
                conn.sendall(RpcProtocol.encode(reply))
        except Exception as e:
            logger.debug("rpc client %s error: %s", addr, e)
        finally:
            logger.info("rpc client disconnected: %s", addr)
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, rpc_id: int, method: str, args: list) -> dict:
        handler_name = f"rpc_{method}"
        handler = getattr(self.service, handler_name, None)

        if handler is None:
            return RpcProtocol.make_reply(rpc_id, error=f"Unknown method: {method}")

        try:
            result = handler(*args)
            return RpcProtocol.make_reply(rpc_id, result=result)
        except TypeError as e:
            return RpcProtocol.make_reply(rpc_id, error=f"Bad arguments: {e}")
        except ValueError as e:
            return RpcProtocol.make_reply(rpc_id, error=str(e))
        except Exception as e:
            logger.exception("rpc dispatch error: method=%s", method)
            return RpcProtocol.make_reply(rpc_id, error=f"Internal error: {e}")
