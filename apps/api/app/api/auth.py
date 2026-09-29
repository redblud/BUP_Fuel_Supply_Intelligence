"""Operator access control for consequential routes: approve, reject, mode and kill switch, and demo controls.

A shared secret (`OPERATOR_TOKEN`) is sent as `X-Operator-Token`. With no token configured the routes stay closed, so a
missing setting can never open them. `X-Operator-Name` is a label for the audit trail; it is not authenticated.
"""

import json
import logging
import secrets

from fastapi import Depends, Header, HTTPException

from app.api.deps import AppContext, get_ctx

log = logging.getLogger(__name__)

NAME_MAX = 64


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def check_token(configured: str, presented: str | None) -> HTTPException | None:
    """Pure: None when `presented` matches the configured token, otherwise the refusal to raise."""
    if not configured:
        return _refuse(401, "OPERATOR_AUTH_NOT_CONFIGURED", "Operator controls are closed: OPERATOR_TOKEN is not set on the server.")
    if not presented:
        return _refuse(401, "OPERATOR_TOKEN_REQUIRED", "This action needs the operator token.")
    if not secrets.compare_digest(presented.encode(), configured.encode()):
        return _refuse(401, "OPERATOR_TOKEN_INVALID", "The operator token was not accepted.")
    return None


async def require_operator(
    ctx: AppContext = Depends(get_ctx),
    x_operator_token: str | None = Header(default=None),
    x_operator_name: str | None = Header(default=None),
) -> str:
    """FastAPI dependency: returns the actor label for the audit record, or raises 401 in the shared error shape."""
    refusal = check_token(ctx.settings.operator_token, x_operator_token)
    if refusal is not None:
        raise refusal
    return (x_operator_name or "operator").strip()[:NAME_MAX] or "operator"


async def require_demo_controls(actor: str = Depends(require_operator), ctx: AppContext = Depends(get_ctx)) -> str:
    """Demo controls also need DEMO_CONTROLS_ENABLED=true; otherwise 403 even with a valid token."""
    if not ctx.settings.demo_controls_enabled:
        raise _refuse(403, "DEMO_CONTROLS_DISABLED", "Demo controls are disabled on this server.")
    return actor


async def audit(ctx: AppContext, actor: str, action: str, target: str, outcome: str = "accepted") -> None:
    """Record who took a consequential action. A failing database is surfaced as PERSISTENCE_FAILURE, never a crash."""
    log.info("operator action", extra={"component": "operator", "decision_id": target})
    try:
        await ctx.db.record("operator_action", json.dumps({"actor": actor, "action": action, "target": target, "outcome": outcome}))
    except Exception as exc:  # noqa: BLE001
        ctx.state.persistence_error = f"{type(exc).__name__}: {exc}"
        log.error("audit write failed", extra={"component": "persistence", "error_code": "PERSISTENCE_FAILURE"})
