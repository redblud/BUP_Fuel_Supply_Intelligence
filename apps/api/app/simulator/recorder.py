"""Record every /v1/* resource of a running simulator into tests/fixtures/recorded-<name>.json.

The output has the same shape as the hand-made fixtures (resource name -> raw body, plus
"health" and "stale"), so FakeSimulatorClient can replay it. Run from apps/api:

    uv run python -m app.simulator.recorder --name baseline --base-url http://localhost:9000
    uv run python -m app.simulator.recorder --name tick-40 --steps 40   # pause first, then step

Stepping uses /admin/step, which only makes sense while the simulator is paused.
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.config import REPO_ROOT
from app.simulator.client import RESOURCES, RealSimulatorClient, SimulatorClient

DEFAULT_BASE_URL = "http://localhost:9000"
DEFAULT_DEMAND_LIMIT = 200


async def record(client: SimulatorClient, demand_limit: int = DEFAULT_DEMAND_LIMIT) -> dict[str, Any]:
    """Read every resource once, sequentially, and return them as a fixture dict.

    `stale` is true if any read carried X-Simulator-Stale. Errors propagate as SimulatorError:
    a partial recording would look like a valid one.
    """
    recording: dict[str, Any] = {"health": await client.get_health()}
    stale = False
    for resource in RESOURCES:
        response = await (client.get(resource, limit=demand_limit) if resource == "demand-history" else client.get(resource))
        recording[resource] = response.body
        stale = stale or response.stale
    recording["stale"] = stale
    return recording


def write_recording(recording: dict[str, Any], path: Path) -> None:
    """Write a recording as stable, diff-friendly JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recording, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> Path:
    client = RealSimulatorClient(args.base_url, connect_timeout=2.0, read_timeout=10.0)
    try:
        for _ in range(args.steps):
            await client.admin("step")
        recording = await record(client, args.demand_limit)
    finally:
        await client.close()
    path = args.out_dir / f"recorded-{args.name}.json"
    write_recording(recording, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="scenario name; writes recorded-<name>.json")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--steps", type=int, default=0, help="POST /admin/step this many times before recording")
    parser.add_argument("--demand-limit", type=int, default=DEFAULT_DEMAND_LIMIT)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "tests" / "fixtures")
    print(f"wrote {asyncio.run(_run(parser.parse_args()))}")


if __name__ == "__main__":
    main()
