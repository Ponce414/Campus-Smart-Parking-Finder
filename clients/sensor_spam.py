import argparse
import socket
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5001)  # your log shows sensor on 5001
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--lots", nargs="+", default=["LOT-A", "LOT-B", "LOT-C"])
    ap.add_argument("--rate", type=float, default=10.0, help="updates/sec/lot")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((args.host, args.port))

    # We alternate +1/-1 so occupancy doesn't just clamp at capacity/0.
    # Total updates/sec = rate * num_lots.
    period = 1.0 / (args.rate * len(args.lots))
    end_t = time.perf_counter() + args.duration
    sign = 1
    sent = 0

    try:
        i = 0
        while time.perf_counter() < end_t:
            lot = args.lots[i % len(args.lots)]
            delta = sign
            sign *= -1
            msg = f"UPDATE {lot} {delta}\n".encode("utf-8")
            s.sendall(msg)
            sent += 1
            i += 1
            time.sleep(period)
    finally:
        s.close()

    print(f"sent {sent} updates over {args.duration:.0f}s "
          f"({sent/args.duration:.1f} total updates/sec, "
          f"target {args.rate} updates/sec/lot)")


if __name__ == "__main__":
    main()