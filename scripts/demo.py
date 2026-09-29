"""Repeatable demo and recovery scenario against the running stack (real simulator).

    make up-sim            # SIMULATOR_MODE=real, OPERATOR_TOKEN and DEMO_CONTROLS_ENABLED set in .env
    cd apps/api && uv run python ../../scripts/demo.py

Steps: reset, run, inject demand_spike + route_disruption and watch the recommendations reroute, inject stale_data and
unavailable faults and watch the Tripwire trip and clear, then approve one recommendation and follow its allocation to
ARRIVED. Prints one line per step and exits non-zero on the first step that does not behave as expected.

Simulator admin calls (reset, events, faults) go straight to the simulator: they are organizer tools that the API does not
proxy. Everything the operator would do goes through the API.
"""

import argparse
import os
import sys
import time

import httpx


class Demo:
    def __init__(self, api: str, sim: str, token: str) -> None:
        self.api = httpx.Client(base_url=api, headers={"X-Operator-Token": token, "X-Operator-Name": "demo-script"}, timeout=30)
        self.sim = httpx.Client(base_url=sim, timeout=30)

    def step(self, name: str) -> None:
        print(f"==> {name}", flush=True)

    def fail(self, message: str) -> None:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)

    def dashboard(self) -> dict:
        return self.api.get("/api/dashboard").json()

    def wait(self, what: str, done, timeout: float = 60.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            d = self.dashboard()
            if done(d):
                return d
            time.sleep(1.0)
        self.fail(f"timed out waiting for {what}")
        raise SystemExit(1)

    def admin(self, path: str, **body: object) -> dict:
        r = self.sim.post(path, json=body or None)
        r.raise_for_status()
        return r.json()

    def steps(self, n: int) -> None:
        for _ in range(n):
            self.admin("/admin/step")

    def run(self) -> None:
        self.step("reset and pause")
        self.admin("/admin/reset")
        self.admin("/admin/pause")
        self.steps(2)
        d = self.wait("a trusted snapshot", lambda d: d["state"] is not None and d["tripwire"]["state"] == "CLEAR")
        before = sorted(r["request"]["route_id"] for r in d["plan"]["recommendations"])
        print(f"    routes before the crisis: {before}")

        self.step("inject demand_spike + route_disruption")
        tick = d["state"]["run"]["tick"]
        self.admin("/admin/events", type="demand_spike", start_tick=tick + 1, duration_ticks=24, parameters={"multiplier": 1.8})
        self.admin("/admin/events", type="route_disruption", start_tick=tick + 1, duration_ticks=24, parameters={})
        self.steps(3)
        d = self.wait("a replanned dashboard", lambda d: d["plan"] is not None and d["state"]["run"]["tick"] > tick)
        after = sorted(r["request"]["route_id"] for r in d["plan"]["recommendations"])
        print(f"    routes after the crisis:  {after}")
        if before == after:
            self.fail("recommendations did not change after the crisis events")

        self.step("stale_data fault trips the guard")
        self.admin("/admin/faults", type="stale_data", duration_seconds=120)
        d = self.wait("SNAPSHOT_STALE", lambda d: d["tripwire"]["state"] == "TRIPPED")
        print(f"    tripped: {[t['code'] for t in d['tripwire']['trips'] if t['severity'] == 'CRITICAL']}")

        self.step("unavailable fault, then clear and resync")
        self.admin("/admin/faults", type="unavailable", duration_seconds=120)
        self.wait("SIMULATOR_UNAVAILABLE", lambda d: any(t["code"] == "SIMULATOR_UNAVAILABLE" for t in d["tripwire"]["trips"]))
        self.admin("/admin/faults/clear")
        d = self.wait("the guard to clear after a full resync", lambda d: d["tripwire"]["state"] == "CLEAR")
        print("    guard clear, automation may resume")

        self.step("approve one recommendation")
        rec = d["plan"]["recommendations"][0]
        r = self.api.post(f"/api/recommendations/{rec['id']}/approve")
        if r.status_code != 200:
            self.fail(f"approve returned {r.status_code}: {r.text}")
        allocation_id = r.json()["allocation_id"]
        print(f"    allocation {allocation_id} created")

        self.step("follow the allocation to ARRIVED")
        for _ in range(200):
            self.steps(1)
            states = self.api.get("/api/decisions").json()
            status = next((s["allocation_status"] for s in states if s["allocation_id"] == allocation_id), None)
            if status == "ARRIVED":
                print("    ARRIVED")
                return
        self.fail("the allocation never reached ARRIVED")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=os.environ.get("API_URL", "http://127.0.0.1:8000"))
    p.add_argument("--sim", default=os.environ.get("SIM_URL", "http://127.0.0.1:9000"))
    p.add_argument("--token", default=os.environ.get("OPERATOR_TOKEN", ""))
    args = p.parse_args()
    if not args.token:
        sys.exit("Set OPERATOR_TOKEN (the same value as the server) or pass --token.")
    Demo(args.api, args.sim, args.token).run()


if __name__ == "__main__":
    main()
