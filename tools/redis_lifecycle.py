"""Bounded persistence setup and clean shutdown for the local Windows store."""

from __future__ import annotations

import argparse
import sys
import time

import redis


def prepare(client: redis.Redis, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.ping()
            break
        except (redis.ConnectionError, redis.TimeoutError):
            if time.monotonic() >= deadline:
                raise TimeoutError("Redis did not become ready") from None
            time.sleep(0.1)
    # Load an existing RDB first, then build AOF from that in-memory state.
    client.config_set("appendfsync", "everysec")
    client.config_set("appendonly", "yes")
    while time.monotonic() < deadline:
        info = client.info("persistence")
        if not info.get("aof_rewrite_in_progress") and not info.get("aof_rewrite_scheduled"):
            if info.get("aof_enabled") and info.get("aof_last_bgrewrite_status") == "ok":
                return
            raise RuntimeError("Redis could not create its append-only file")
        time.sleep(0.1)
    raise TimeoutError("Redis persistence initialization timed out")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "stop"))
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    try:
        with redis.Redis(host="127.0.0.1", port=args.port, socket_connect_timeout=1,
                         socket_timeout=5, decode_responses=True,
                         retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0)) as client:
            if args.action == "prepare":
                prepare(client)
            else:
                client.ping()
                client.shutdown(save=True)
        return 0
    except (redis.RedisError, OSError, RuntimeError) as exc:
        print(f"Redis {args.action} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
