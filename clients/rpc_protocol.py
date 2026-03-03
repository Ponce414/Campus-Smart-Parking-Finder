"""
Minimal RPC protocol: length-prefixed JSON over TCP.

Framing
-------
  4 bytes (big-endian uint32) = payload length
  N bytes                     = JSON payload (UTF-8)

Wire format
-----------
  Request:  {"rpcId": <uint32>, "method": <str>, "args": [<any>, ...]}
  Reply:    {"rpcId": <uint32>, "result": <any>, "error": <str|null>}

Parameter passing notes
-----------------------
  - Length prefix uses network byte order (big-endian), matching the
    convention for TCP protocols.  struct format "!I" = unsigned 32-bit int.
  - All values are JSON-encoded, so types are limited to JSON primitives
    (str, int, float, bool, null, list, dict).  The caller and skeleton
    must agree on argument order per method.
  - Strings are UTF-8.  Integers fit comfortably within JSON's number type.

RPC path
--------
  Caller -> Client Stub -> TCP -> Server Skeleton -> Method -> Return -> Client Stub -> Caller
"""

import json
import socket
import struct
import threading
from typing import Any, Dict, Optional

HEADER_SIZE = 4          # 4-byte big-endian length prefix
MAX_PAYLOAD = 4_194_304  # 4 MB safety limit


# ------------------------------------------------------------------ helpers

def _recv_exactly(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly *n* bytes from *sock*. Returns ``None`` on clean EOF."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


# ------------------------------------------------------------------ codec

class RpcProtocol:
    """Encode / decode length-prefixed JSON frames."""

    @staticmethod
    def encode(msg: Dict[str, Any]) -> bytes:
        """Return ``header + payload`` bytes for *msg*."""
        payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        header = struct.pack("!I", len(payload))
        return header + payload

    @staticmethod
    def decode_from_socket(sock: socket.socket) -> Optional[Dict[str, Any]]:
        """Read one complete frame from *sock*. Returns ``None`` on EOF."""
        header = _recv_exactly(sock, HEADER_SIZE)
        if header is None:
            return None
        length = struct.unpack("!I", header)[0]
        if length > MAX_PAYLOAD:
            raise ValueError(f"Frame too large: {length} bytes")
        payload = _recv_exactly(sock, length)
        if payload is None:
            return None
        return json.loads(payload.decode("utf-8"))

    # ---------- convenience builders

    @staticmethod
    def make_request(rpc_id: int, method: str, args: list) -> Dict:
        return {"rpcId": rpc_id, "method": method, "args": args}

    @staticmethod
    def make_reply(rpc_id: int, result: Any = None, error: Optional[str] = None) -> Dict:
        return {"rpcId": rpc_id, "result": result, "error": error}


# ------------------------------------------------------------------ errors

class RpcTimeoutError(Exception):
    """Raised when an RPC call exceeds its deadline."""
    pass


# ------------------------------------------------------------------ client stub

class RpcClientStub:
    """
    Client-side RPC stub.

    Usage::

        stub = RpcClientStub("localhost", 9001)
        lots = stub.call("getLots")
        stub.close()
    """

    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((host, port))
        self._sock.settimeout(timeout)
        self._timeout = timeout
        self._next_id = 1
        self._lock = threading.Lock()

    def call(self, method: str, *args) -> Any:
        """Synchronous RPC call.  Raises ``RpcTimeoutError`` on timeout."""
        with self._lock:
            rpc_id = self._next_id
            self._next_id += 1

        request = RpcProtocol.make_request(rpc_id, method, list(args))
        frame = RpcProtocol.encode(request)

        try:
            self._sock.sendall(frame)
            reply = RpcProtocol.decode_from_socket(self._sock)
        except socket.timeout:
            raise RpcTimeoutError(
                f"RPC call '{method}' timed out after {self._timeout}s"
            )

        if reply is None:
            raise ConnectionError("Server closed connection")

        if reply.get("error"):
            raise RuntimeError(f"RPC error: {reply['error']}")

        return reply.get("result")

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass
