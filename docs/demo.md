# Running the demo

An offline demo that needs no Docker and no real simulator. It uses the **demo simulator**
(`SIMULATOR_MODE=demo`, `apps/api/app/simulator/demo.py`): a small stateful stand-in that starts from the
`route-disruption` fixture (Dhaka demand spike, `route-gazipur-mirpur` disrupted, Tongi diesel at risk), accepts
allocations, ticks on request, and delivers fuel. It is not the real simulator; use `make up-sim` for that.

## Start

Two terminals from the repo root:

```bash
make demo-backend
```

```bash
make demo-frontend
```

Open the URL Vite prints (normally http://localhost:5173). The operator token is `demo`.

## Walkthrough

1. **Read the situation.** The overview shows Tongi diesel heading for a stockout, the disrupted Gazipur to Mirpur
   route, and a proposed shipment from Gazipur to Tongi with the reasoning under "Why this recommendation?".
2. **Unlock the controls.** Paste `demo` into **Operator token**. Without it, approve, reject, mode and simulator
   buttons answer 401 with a clear message.
3. **Manual Demo.** Set the operating mode to **Manual demo** and press **Approve whole plan**. Each recommendation is
   revalidated on a fresh snapshot right before it is sent; the panel reports how many executed or were refused.
4. **Watch it arrive.** Under Simulator demo press **Step** a few times (or **Run** to tick every two seconds). The
   decision history shows the allocation move PENDING, IN_TRANSIT, ARRIVED in the Delivery column, and Tongi's stock
   recovers.
5. **Guarded Auto.** Switch to **Guarded auto**: the loop approves new proposals by itself, only while the guard is
   clear, skips any that changed materially since first proposed, and every action is audited as `guarded-auto`.
6. **Kill switch.** Turn the kill switch on: nothing executes in any mode, and it survives a restart.
7. **Safety guard.** `GET /metrics` shows the counters; stop the backend mid-run and restart it to see the lifecycle and
   kill switch reload from SQLite.

## Same flow over the API

```bash
H='X-Operator-Token: demo'; J='Content-Type: application/json'
curl -s -X PUT localhost:8000/api/automation -H "$H" -H "$J" -d '{"mode":"MANUAL_DEMO","kill_switch":false}'
curl -s -X POST localhost:8000/api/plan/approve -H "$H"
curl -s -X POST localhost:8000/api/sim/control -H "$H" -H "$J" -d '{"action":"step"}'
curl -s localhost:8000/api/decisions
```

The demo simulator supports `run`, `pause` and `step`. Crisis events, faults and reset need the real simulator
(`scripts/demo.py` and `tests/scenarios/real_faults.py` drive those).
