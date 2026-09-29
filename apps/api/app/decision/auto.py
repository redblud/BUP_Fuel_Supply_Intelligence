"""Guarded automation: execute proposed recommendations on a timer, only while it is safe.

Every order still goes through `orchestrator.approve`, so it gets the same revalidation, Tripwire, kill switch,
expiry, depot lock, and idempotency checks as a manual approval. This loop only decides when to ask.
"""

import asyncio
import contextlib
import logging

from app.api.auth import audit
from app.api.deps import AppContext
from app.decision import orchestrator

log = logging.getLogger(__name__)

AUTO_ACTOR = "guarded-auto"


async def auto_step(ctx: AppContext) -> list[str]:
    """One pass. Returns the ids executed. Does nothing unless mode is GUARDED_AUTO and the kill switch is off."""
    if ctx.automation.mode != "GUARDED_AUTO" or ctx.automation.kill_switch:
        return []
    executed: list[str] = []
    proposed = [rid for rid, st in ctx.recommendation_states.items() if st.status == "PROPOSED"]
    if not proposed:  # nothing registered yet: let a dashboard poll or a fresh plan register them
        state = await ctx.state.refresh()
        if state is None:
            return []
        from app.intelligence import build_plan

        plan = build_plan(state)
        orchestrator.sync_plan(ctx, plan)
        proposed = [rid for rid, st in ctx.recommendation_states.items() if st.status == "PROPOSED"]
    for rid in proposed:
        if rid in ctx.review_required:  # changed since first proposed: only a person may approve it
            continue
        if ctx.automation.mode != "GUARDED_AUTO" or ctx.automation.kill_switch:
            break  # the operator changed their mind mid pass
        try:
            result = await orchestrator.approve(ctx, rid)
        except orchestrator.DecisionError as exc:
            log.info("auto skipped %s: %s", rid, exc.code)
            await audit(ctx, AUTO_ACTOR, "auto_approve", rid, f"refused:{exc.code}")
            if exc.code in ("TRIPWIRE_TRIPPED", "KILL_SWITCH", "SNAPSHOT_UNAVAILABLE", "EXECUTION_UNKNOWN"):
                break  # nothing else can safely run this pass
            continue
        await audit(ctx, AUTO_ACTOR, "auto_approve", rid, result.status)
        if result.status == "DONE":
            executed.append(rid)
    return executed


class AutoRunner:
    """Background loop around `auto_step`. A crash in one pass never stops the loop."""

    def __init__(self, ctx: AppContext, interval: float) -> None:
        self._ctx = ctx
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="guarded-auto")

    async def _run(self) -> None:
        while True:
            try:
                await auto_step(self._ctx)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("guarded auto pass failed")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
