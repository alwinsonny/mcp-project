"""Run two copies of the server behind the round-robin balancer, on this computer.

    make cluster        (or: python -m merch_platform.devtools.cluster)

    agent ──> balancer :8080 ──┬──> copy "a" :8001 ──┐
                               └──> copy "b" :8002 ──┴──> Postgres

Three ordinary processes: no Docker needed. Both copies read the same .env, so they
share the database and MERCH_REQUEST_STATE_KEYS. Press Ctrl+C to stop all three.

It refuses to start without MERCH_REQUEST_STATE_KEYS, because two copies with
separate random keys would break every confirmation that switches copies.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

import httpx2

from merch_platform.config import get_settings


def wait_until_healthy(url: str, process: subprocess.Popen[bytes], seconds: float = 30) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False  # it exited: the error is already in the terminal
        try:
            if httpx2.get(url, timeout=1).status_code == 200:
                return True
        except httpx2.TransportError:
            pass
        time.sleep(0.3)
    return False


def stop_on_signal(signum: int, _frame: object) -> None:
    """Turn "terminate" / "terminal closed" into a normal exit, so `finally` runs.

    Python stops at once on SIGTERM by default, skipping clean-up, which would leave
    the three child processes running (and holding database connections).
    """
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, stop_on_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, stop_on_signal)
    parser = argparse.ArgumentParser(description="Two server copies behind a balancer.")
    parser.add_argument("--port", type=int, default=8080, help="Balancer port.")
    parser.add_argument("--backend-ports", default="8001,8002")
    args = parser.parse_args(argv)

    if not get_settings().request_state_key_list():
        print(
            "MERCH_REQUEST_STATE_KEYS is not set. Every copy needs the same key.\n"
            "Run `make keygen` and put the result in .env as MERCH_REQUEST_STATE_KEYS=...",
            file=sys.stderr,
        )
        return 2

    python = sys.executable
    ports = [int(p) for p in args.backend_ports.split(",")]
    names = [chr(ord("a") + i) for i in range(len(ports))]
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for name, port in zip(names, ports, strict=True):
            server = subprocess.Popen(
                [
                    python,
                    "-m",
                    "merch_platform.server.http",
                    "--port",
                    str(port),
                    "--instance",
                    name,
                ]
            )
            processes.append(server)
            if not wait_until_healthy(f"http://127.0.0.1:{port}/health", server):
                print(f"copy {name} on port {port} did not become healthy.", file=sys.stderr)
                return 1
        backends = ",".join(f"http://127.0.0.1:{p}" for p in ports)
        processes.append(
            subprocess.Popen(
                [
                    python,
                    "-m",
                    "merch_platform.devtools.balancer",
                    "--port",
                    str(args.port),
                    "--backends",
                    backends,
                ]
            )
        )
        if not wait_until_healthy(f"http://127.0.0.1:{args.port}/health", processes[-1]):
            print("the balancer did not start.", file=sys.stderr)
            return 1
        print(
            f"\nReady: http://127.0.0.1:{args.port}/mcp  (copies {', '.join(names)}). "
            "Ctrl+C to stop.\n",
            flush=True,
        )
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        print("A process stopped; shutting the others down.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # Ctrl+C
        return 0
    finally:  # runs on Ctrl+C, SIGTERM, SIGHUP and errors alike: stop every child
        for p in processes:
            if p.poll() is None:
                p.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)
        for p in processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
