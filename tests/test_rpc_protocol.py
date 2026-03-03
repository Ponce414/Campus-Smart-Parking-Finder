"""Tests for RpcProtocol framing: encode/decode round-trip and partial reads."""

import io
import socket
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients"))

from rpc_protocol import RpcProtocol, HEADER_SIZE


# ---------------------------------------------------------------- round-trip


def test_encode_decode_round_trip():
    """Encoding then decoding should produce the original dict."""
    msg = {"rpcId": 42, "method": "getLots", "args": []}
    frame = RpcProtocol.encode(msg)

    # Simulate socket reads with a FakeSocket
    sock = FakeSocket(frame)
    decoded = RpcProtocol.decode_from_socket(sock)
    assert decoded == msg


def test_multiple_frames():
    """Two consecutive frames on the same stream should both decode."""
    m1 = {"rpcId": 1, "method": "reserve", "args": ["LOT-A", "P1"]}
    m2 = {"rpcId": 2, "method": "cancel", "args": ["LOT-A", "P1"]}
    stream = RpcProtocol.encode(m1) + RpcProtocol.encode(m2)

    sock = FakeSocket(stream)
    assert RpcProtocol.decode_from_socket(sock) == m1
    assert RpcProtocol.decode_from_socket(sock) == m2


# ---------------------------------------------------------------- edge cases


def test_eof_returns_none():
    """Empty stream should return None (clean EOF)."""
    sock = FakeSocket(b"")
    assert RpcProtocol.decode_from_socket(sock) is None


def test_partial_header_returns_none():
    """Truncated header should return None."""
    sock = FakeSocket(b"\x00\x00")
    assert RpcProtocol.decode_from_socket(sock) is None


def test_partial_payload_returns_none():
    """Header says 100 bytes but only 5 bytes follow."""
    header = struct.pack("!I", 100)
    sock = FakeSocket(header + b"short")
    assert RpcProtocol.decode_from_socket(sock) is None


def test_too_large_frame_raises():
    """Frame exceeding MAX_PAYLOAD should raise ValueError."""
    header = struct.pack("!I", 5_000_000)
    sock = FakeSocket(header)
    try:
        RpcProtocol.decode_from_socket(sock)
        assert False, "should have raised"
    except ValueError:
        pass


# ---------------------------------------------------------------- helpers


def test_make_request():
    req = RpcProtocol.make_request(1, "getLots", [])
    assert req == {"rpcId": 1, "method": "getLots", "args": []}


def test_make_reply():
    rep = RpcProtocol.make_reply(1, result=[1, 2, 3])
    assert rep == {"rpcId": 1, "result": [1, 2, 3], "error": None}


def test_make_reply_error():
    rep = RpcProtocol.make_reply(1, error="oops")
    assert rep == {"rpcId": 1, "result": None, "error": "oops"}


# ---------------------------------------------------------------- FakeSocket


class FakeSocket:
    """Mimics socket.recv() behaviour from a bytes buffer.

    Delivers data in small random-ish chunks to exercise partial-read logic.
    """

    def __init__(self, data: bytes, chunk_size: int = 7):
        self._buf = data
        self._pos = 0
        self._chunk = chunk_size

    def recv(self, n: int) -> bytes:
        end = min(self._pos + min(n, self._chunk), len(self._buf))
        chunk = self._buf[self._pos:end]
        self._pos = end
        return chunk
