import asyncio
from collections import Counter
from dataclasses import dataclass, field

from fastapi import Request

from app.core.config import Settings
from app.domain.models import AutomationState, Recommendation, RecommendationState
from app.persistence.database import Database
from app.state.service import StateService


@dataclass
class AppContext:
    settings: Settings
    db: Database
    state: StateService
    automation: AutomationState
    recommendation_states: dict[str, RecommendationState]
    depot_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    counters: Counter[str] = field(default_factory=Counter)
    planner_seconds: float | None = None
    baselines: dict[tuple[str, str, int], Recommendation] = field(default_factory=dict)
    review_required: frozenset[str] = frozenset()


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
