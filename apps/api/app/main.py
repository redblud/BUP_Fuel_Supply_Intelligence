from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import AppContext
from app.api.routes import health, history, operations
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.decision.auto import AutoRunner
from app.domain.models import AutomationState
from app.persistence.database import Database
from app.simulator.client import RealSimulatorClient, SimulatorClient
from app.simulator.fake import FakeSimulatorClient
from app.state.service import StateService
from app.state.sync import StateSync


def build_client(settings: Settings) -> SimulatorClient:
    if settings.simulator_mode == "real":
        return RealSimulatorClient(
            settings.simulator_base_url,
            settings.simulator_connect_timeout,
            settings.simulator_read_timeout,
            max_retries=settings.simulator_max_retries,
            backoff_base=settings.simulator_backoff_base,
            backoff_max=settings.simulator_backoff_max,
        )
    return FakeSimulatorClient(settings.fixture_dir, settings.fixture_scenario)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        await db.init()
        client = build_client(settings)
        app.state.ctx = AppContext(
            settings=settings,
            db=db,
            state=StateService(
                client,
                settings.snapshot_tick_tolerance,
                fixture=settings.simulator_mode == "fake",
                db=db,
                history_ticks=settings.demand_history_ticks,
            ),
            automation=AutomationState(mode=settings.automation_default_mode, kill_switch=False),
            recommendation_states={},
        )
        sync = None
        if settings.simulator_mode == "real":  # fixtures are static: read them on demand
            sync = StateSync(
                app.state.ctx.state,
                fallback_interval=settings.sync_fallback_interval,
                resync_interval=settings.sync_resync_interval,
                min_refresh_gap=settings.sync_min_refresh_gap,
            )
            sync.start()
        auto = AutoRunner(app.state.ctx, settings.auto_interval)
        auto.start()
        yield
        await auto.stop()
        if sync is not None:
            await sync.stop()
        await client.close()
        await db.close()

    app = FastAPI(title="FuelOps AI API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(health.router)
    app.include_router(operations.router)
    app.include_router(history.router)
    return app


app = create_app()
