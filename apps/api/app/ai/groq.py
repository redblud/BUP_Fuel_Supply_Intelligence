"""Groq briefing: turns the current snapshot, plan and Tripwire into a short operator briefing (or answers one question).

Advisory only. The planner and Tripwire stay deterministic and decide everything; this text is a readable summary of what
they already computed, and it is always labelled as AI-generated. `build_messages` is pure; `complete` is the only I/O.
"""

import httpx

from app.core.config import Settings
from app.domain.models import NetworkState, Plan, TripwireStatus

SYSTEM_PROMPT = (
    "You are a briefing assistant for a fuel-supply operations console in Bangladesh. You are given the current network "
    "snapshot summary, risk assessments, the planner's recommendations, the cases the planner could not serve, and the "
    "safety guard status, all as JSON. Rules: use only the numbers and names in that JSON; never invent stations, "
    "quantities or ticks; if something is not in the data say it is not known. You cannot approve or execute anything and "
    "must not tell the operator that you did. If the data freshness is not FRESH or FIXTURE, or the guard is TRIPPED, say "
    "so first. Be concise: at most 8 short lines, plain language, most urgent first."
)

MAX_RISKS = 8


class AiError(Exception):
    """An expected failure of the AI layer, mapped to one HTTP error shape by the route."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def build_context(state: NetworkState, plan: Plan, trip: TripwireStatus) -> dict[str, object]:
    """Pure: the compact, derived facts sent to the model. No raw simulator payloads, credentials or operator identity."""
    urgent = [r for r in plan.risks if r.level in ("CRITICAL", "HIGH")]
    others = [r for r in plan.risks if r.level not in ("CRITICAL", "HIGH")]
    risks = (urgent + others)[:MAX_RISKS]
    return {
        "tick": state.run.tick,
        "run_id": state.meta.run_id,
        "data_freshness": state.meta.freshness,
        "guard": {
            "state": trip.state,
            "trips": [{"code": t.code, "severity": t.severity, "scope": t.scope, "message": t.message} for t in trip.trips],
        },
        "risks": [
            {
                "station": r.station_id,
                "fuel": r.fuel_type,
                "level": r.level,
                "inventory_l": round(r.current_inventory),
                "safety_stock_l": round(r.safety_stock),
                "hours_to_stockout": r.hours_to_stockout,
                "reasons": list(r.reason_codes),
            }
            for r in risks
        ],
        "recommendations": [
            {
                "id": rec.id,
                "station": rec.station_id,
                "fuel": rec.fuel_type,
                "priority": rec.priority,
                "send_l": round(rec.request.quantity),
                "from_depot": rec.request.source_depot_id,
                "route": rec.request.route_id,
                "arrives_tick": rec.expected_arrival_tick,
                "summary": rec.summary,
            }
            for rec in plan.recommendations
        ],
        "not_served": [{"station": b.station_id, "fuel": b.fuel_type, "why": b.message} for b in plan.blocked],
        "planner": plan.planner_version,
    }


def build_messages(context: dict[str, object], question: str | None) -> list[dict[str, str]]:
    """Pure: chat messages for the model."""
    import json

    ask = question.strip() if question and question.strip() else "Give the operator a short briefing on the current situation and what to do first."
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Data:\n{json.dumps(context, separators=(',', ':'))}\n\nRequest: {ask}"},
    ]


async def complete(client: httpx.AsyncClient, settings: Settings, messages: list[dict[str, str]]) -> str:
    """One chat completion against Groq's OpenAI-compatible endpoint. Raises AiError; never returns a made-up fallback."""
    if not settings.groq_api_key:
        raise AiError("AI_NOT_CONFIGURED", "The AI briefing is off: GROQ_API_KEY is not set on the server.", 503)
    try:
        response = await client.post(
            f"{settings.groq_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={"model": settings.groq_model, "messages": messages, "temperature": 0.2, "max_tokens": 500},
            timeout=settings.ai_timeout,
        )
    except httpx.TimeoutException as exc:
        raise AiError("AI_TIMEOUT", "The AI service did not answer in time.", 504) from exc
    except httpx.HTTPError as exc:
        raise AiError("AI_UNREACHABLE", f"The AI service could not be reached ({type(exc).__name__}).", 502) from exc
    if response.status_code >= 400:
        raise AiError("AI_UPSTREAM_ERROR", f"The AI service answered {response.status_code}.", 502)
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AiError("AI_BAD_RESPONSE", "The AI service returned an unexpected response.", 502) from exc
    if not isinstance(text, str) or not text.strip():
        raise AiError("AI_BAD_RESPONSE", "The AI service returned an empty answer.", 502)
    return text.strip()
