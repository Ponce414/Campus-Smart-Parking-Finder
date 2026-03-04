import argparse
import statistics
import threading
import time
from typing import List, Tuple

from rpc_protocol import RpcClientStub, RpcTimeoutError


def percentile(sorted_vals: List[float], p: float) -> float:
    """Nearest-rank percentile; p in [0,1]."""
    if not sorted_vals:
        return float("nan")
    k = int((len(sorted_vals) - 1) * p)
    return sorted_vals[k]


def worker_loop(host: str, port: int, duration_s: float, method: str, lot_id: str, wid: int):
    """
    Returns (ok_requests, latencies_ms[], timeouts, errors)
    """
    stub = RpcClientStub(host, port, timeout=30.0)  # was 5.0
    end_t = time.perf_counter() + duration_s

    lats_ms: List[float] = []
    ok = 0
    timeouts = 0
    errors = 0

    plate_counter = 0

    try:
        while time.perf_counter() < end_t:
            t0 = time.perf_counter()
            try:
                if method == "getAvailability":
                    _ = stub.call("getAvailability", lot_id)
                elif method == "reserve":
                    plate_counter += 1
                    plate = f"W{wid}-P{plate_counter}"
                    _ = stub.call("reserve", lot_id, plate)
                else:
                    raise ValueError(f"Unknown method: {method}")

                t1 = time.perf_counter()
                lats_ms.append((t1 - t0) * 1000.0)
                ok += 1

            except RpcTimeoutError:
                timeouts += 1
            except Exception:
                errors += 1
    finally:
        stub.close()

    return ok, lats_ms, timeouts, errors

def run_test(host: str, port: int, duration_s: float, workers: int, method: str, lot_id: str):
    threads = []
    results = []
    lock = threading.Lock()

    def target(wid: int):
        r = worker_loop(host, port, duration_s, method, lot_id, wid)
        with lock:
            results.append(r)

    # start timer
    t_start = time.perf_counter()

    # launch worker threads
    for i in range(workers):
        t = threading.Thread(target=target, args=(i + 1,))
        t.start()
        threads.append(t)

    # wait for all threads to finish
    for t in threads:
        t.join()

    # stop timer
    t_end = time.perf_counter()

    wall = t_end - t_start

    # aggregate results
    total_ok = sum(r[0] for r in results)
    all_lats = [lat for r in results for lat in r[1]]
    total_timeouts = sum(r[2] for r in results)
    total_errors = sum(r[3] for r in results)

    total_attempts = total_ok + total_timeouts + total_errors

    all_lats.sort()

    # compute metrics
    throughput = total_ok / wall if wall > 0 else 0.0

    median = statistics.median(all_lats) if all_lats else float("nan")

    p95 = percentile(all_lats, 0.95) if all_lats else float("nan")

    success_rate = (100.0 * total_ok / total_attempts) if total_attempts else 0.0

    print(f"\n=== RPC {method} | workers={workers} | duration={duration_s:.0f}s ===")
    print(f"attempts:   {total_attempts}")
    print(f"success:    {total_ok}  ({success_rate:.2f}%)")
    print(f"timeouts:   {total_timeouts}")
    print(f"errors:     {total_errors}")
    print(f"throughput: {throughput:.2f} req/s")
    print(f"latency:    median={median:.2f} ms   p95={p95:.2f} ms")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)  # your log shows RPC on 5000
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8, 16])
    ap.add_argument("--method", choices=["getAvailability", "reserve"], required=True)
    ap.add_argument("--lot", default="LOT-A")
    args = ap.parse_args()

    for w in args.workers:
        run_test(args.host, args.port, args.duration, w, args.method, args.lot)


if __name__ == "__main__":
    main()