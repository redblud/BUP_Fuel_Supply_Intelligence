import asyncio
from dataclasses import dataclass, field

from fastapi import Request

from app.core.config import Settings
from app.domain.models import AutomationState, RecommendationState
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


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
