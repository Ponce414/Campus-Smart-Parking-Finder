# Campus Smart Parking Finder

A multithreaded campus parking management system built entirely with the Python standard library. Provides four TCP server components sharing a single in-memory state:

| Server | Default Port | Purpose |
|---|---|---|
| Text protocol | 9000 | Newline-delimited command interface |
| RPC | 9001 | Length-prefixed JSON RPC |
| Sensor ingest | 9002 | Asynchronous occupancy updates |
| Event notifier | 9003 | Pub/sub event delivery |

## Architecture

- text clients -> TextServer -> ParkingService -> ParkingState
- RPC clients -> RpcServer -> ParkingService -> ParkingState
- sensors -> SensorServer -> update Queue -> worker -> ParkingState
- ParkingService / worker -> PubSub -> Notifier -> subscribers

### Concurrency model

- **Text & RPC servers** use a bounded `ThreadPoolExecutor` (default 10 workers).  This caps resource usage under load while still supporting concurrent connections.
- **Sensor server** has a dedicated accept thread that reads UPDATE lines and enqueues `(lotId, delta, timestamp)` tuples into a bounded `queue.Queue`.  Configurable worker threads drain the queue, apply state changes, and publish events—keeping sensor I/O fully decoupled from request handling.
- **Event notifier** spawns one daemon thread per subscriber that blocks on the subscriber's queue and writes to its socket.

### Reservation expiry

Reservations automatically expire after a configurable TTL (default 300 s / 5 min).  Expired reservations are purged lazily inside every state-reading operation (under the global lock), so no overbooking is possible.

## RPC Protocol

### Framing

Each frame consists of: 4 bytes (big-endian payload length) -> N bytes JSON payload (UTF-8 request or reply)

- Length prefix: **big-endian unsigned 32-bit int** (`struct.pack("!I", …)`), following the network byte order convention.
- All values are JSON-encoded: `str`, `int`, `float`, `bool`, `null`, `list`, `dict`.
- Strings are UTF-8.

### Wire formats

**Request:**
```json
{ "rpcId": 1, "method": "reserve", "args": ["LOT-A", "ABC-1234"] }
```

**Reply:**
```json
{ "rpcId": 1, "result": true, "error": null }
```

### RPC methods

| Method | Args | Returns |
|---|---|---|
| `getLots` | – | `[{id, capacity, occupied, free}, …]` |
| `getAvailability` | `lotId` | `int` (free spaces) |
| `reserve` | `lotId, plate` | `bool` |
| `cancel` | `lotId, plate` | `bool` |
| `subscribe` | `lotId` | `int` (subId) |
| `unsubscribe` | `subId` | `bool` |

### RPC path

Caller -> Client Stub -> TCP -> Server Skeleton -> Method -> Return -> Client Stub -> Caller

## Pub/Sub

1. Client calls `subscribe(lotId)` via RPC → receives `subId`.
2. Client opens a **separate TCP connection** to the event port (9003).
3. Sends `SUB <subId>\n`.
4. Receives `OK\n` on success.
5. Receives newline-delimited events: `EVENT <lotId> <free> <timestamp>\n`.
6. On disconnect the subscription is automatically removed.

**Back-pressure:** each subscriber has a bounded `queue.Queue(maxsize=64)`.  When full, the **oldest event is dropped** to make room for the newest one.  Drops are counted and logged.

## Setup & Run (macOS)

```bash
# Clone & enter
cd Campus-Smart-Parking-Finder

# Create virtual environment (stdlib only — no pip install needed)
python3 -m venv .venv
source .venv/bin/activate

# (Optional) install test dependency
pip install -r requirements.txt

# Start all servers
python clients/server_main.py
```

### Quick test with `nc`

**Text protocol:**
```bash
nc localhost 9000
PING
LOTS
AVAIL LOT-A
RESERVE LOT-A ABC-1234
CANCEL LOT-A ABC-1234
```

**Sensor update:**
```bash
echo "UPDATE LOT-A +1" | nc localhost 9002
```

### Test with RPC client stub

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "clients"))
from rpc_protocol import RpcClientStub

stub = RpcClientStub("localhost", 9001)
print(stub.call("getLots"))
print(stub.call("getAvailability", "LOT-A"))
print(stub.call("reserve", "LOT-A", "ABC-1234"))
print(stub.call("cancel", "LOT-A", "ABC-1234"))
stub.close()
```

### Test pub/sub event subscription

```python
import socket
from rpc_protocol import RpcClientStub

# 1. Subscribe via RPC
stub = RpcClientStub("localhost", 9001)
sub_id = stub.call("subscribe", "LOT-A")
print(f"Subscribed: subId={sub_id}")

# 2. Connect to event port
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 9003))
sock.sendall(f"SUB {sub_id}\n".encode())
print("Handshake:", sock.recv(64).decode().strip())

# 3. Trigger a change (in another terminal: RESERVE LOT-A XYZ via text or RPC)
# 4. Read events
while True:
    data = sock.recv(4096)
    if not data:
        break
    print("Event:", data.decode().strip())
```

## Running tests

```bash
python -m pytest tests/ -v
```

## Configuration

Place a `config.json` next to `server_main.py` (optional):

```json
{
  "lots": [
    {"id": "LOT-A", "capacity": 100},
    {"id": "LOT-B", "capacity": 200}
  ],
  "text_port": 9000,
  "rpc_port": 9001,
  "sensor_port": 9002,
  "event_port": 9003,
  "pool_size": 10,
  "sensor_workers": 2,
  "reservation_ttl": 300,
  "pubsub_queue_size": 64
}
```