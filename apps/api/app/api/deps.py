from dataclasses import dataclass

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
    # TODO(dev4): move lifecycle into decision/lifecycle.py backed by SQLite.
    recommendation_states: dict[str, RecommendationState]


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
