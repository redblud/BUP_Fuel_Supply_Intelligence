"""Fault checks against the real simulator's /admin/faults, through the running API.

    make up-sim
    cd apps/api && uv run python ../../tests/scenarios/real_faults.py

The in-process version of these scenarios is apps/api/tests/test_fault_scenarios.py. Exits non-zero on the first check that fails.
"""

import argparse
import os
import sys
import time

import httpx


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=os.environ.get("API_URL", "http://127.0.0.1:8000"))
    p.add_argument("--sim", default=os.environ.get("SIM_URL", "http://127.0.0.1:9000"))
    args = p.parse_args()
    api, sim = httpx.Client(base_url=args.api, timeout=30), httpx.Client(base_url=args.sim, timeout=30)

    def dashboard() -> dict:
        return api.get("/api/dashboard").json()

    def codes(d: dict) -> list[str]:
        return [t["code"] for t in d["tripwire"]["trips"]]

    def until(name: str, ok, timeout: float = 45.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            d = dashboard()
            if ok(d):
                print(f"ok   {name}")
                return d
            time.sleep(1.0)
        sys.exit(f"FAIL {name}: {codes(dashboard())}")

    def inject(kind: str, **parameters: object) -> None:
        sim.post("/admin/faults", json={"type": kind, "duration_seconds": 120, "parameters": parameters}).raise_for_status()

    def clear() -> None:
        sim.post("/admin/faults/clear").raise_for_status()

    clear()
    until("baseline is trusted and clear", lambda d: d["state"] is not None and d["tripwire"]["state"] == "CLEAR")

    inject("unavailable")
    d = until("unavailable -> SIMULATOR_UNAVAILABLE trip", lambda d: "SIMULATOR_UNAVAILABLE" in codes(d))
    if d["state"] is not None and d["state"]["meta"]["freshness"] == "FRESH":
        sys.exit("FAIL unavailable data was presented as FRESH")
    clear()
    until("recovers after clear", lambda d: d["tripwire"]["state"] == "CLEAR")

    inject("stale_data")
    until("stale_data -> SNAPSHOT_STALE trip", lambda d: "SNAPSHOT_STALE" in codes(d))
    clear()
    until("recovers after clear", lambda d: d["tripwire"]["state"] == "CLEAR")

    inject("error_rate", rate=0.5)
    started = time.time()
    for _ in range(5):
        d = dashboard()
        if d["state"] is not None and d["state"]["meta"]["freshness"] == "FRESH" and d["state"]["meta"]["stale"]:
            sys.exit("FAIL a faulted read was labelled FRESH")
    print(f"ok   error_rate keeps the API responsive ({time.time() - started:.1f}s for 5 polls)")
    clear()

    inject("latency", delay_ms=500)
    started = time.time()
    api.get("/api/health").raise_for_status()
    print(f"ok   latency: /api/health answered in {time.time() - started:.2f}s")
    clear()

    inject("stream_disconnect")
    until("stream_disconnect -> SSE degraded", lambda d: d["health"]["components"]["sse"]["status"] != "HEALTHY", timeout=60.0)
    clear()
    until("SSE resyncs after clear", lambda d: d["health"]["components"]["sse"]["status"] == "HEALTHY", timeout=90.0)
    print("all real-simulator fault checks passed")


if __name__ == "__main__":
    main()
