"""FastAPI application factory.

Wires:
- DB init
- BroadcasterRegistry + JobManager on app.state
- Reconciler runs once on startup
- All routers
- /api/me sanity check
- Static frontend mount (web/dist) if it exists
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .auth import require_auth
from .bootstrap import ensure_default_project
from .broadcaster import BroadcasterRegistry
from .config import get_settings
from .db import init_db
from .jobs import JobManager
from .reconcile import reconcile_jobs
from .schedule_service import ScheduleService
from .services import claude_md
from .routes import allowlist as allowlist_routes
from .routes import jobs as jobs_routes
from .routes import outcomes as outcomes_routes
from .routes import projects as projects_routes
from .routes import schedules as schedules_routes
from .routes import stream as stream_routes
from .routes import tasks as tasks_routes
from .schemas import AuthInfo


def _web_dist_dir() -> Path | None:
    s = get_settings()
    if s.web_dist and s.web_dist.is_dir():
        return s.web_dist
    here = Path(__file__).resolve()
    # repo root: server/agent_harness/main.py → repo/
    candidate = here.parents[2] / "web" / "dist"
    return candidate if candidate.is_dir() else None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    ensure_default_project()
    try:
        claude_md.sync_all()
    except Exception:  # noqa: BLE001
        pass
    settings = get_settings()
    assert settings.logs_dir is not None
    registry = BroadcasterRegistry(settings.logs_dir)
    manager = JobManager(
        broadcasters=registry,
        max_concurrent=settings.max_concurrent_jobs,
        claude_path=settings.claude_path,
        default_extra_args=settings.default_claude_args,
        default_idle_timeout_seconds=settings.idle_timeout_seconds,
    )
    app.state.broadcasters = registry
    app.state.job_manager = manager
    await reconcile_jobs(registry)
    schedules = ScheduleService(manager)
    schedules.start()
    app.state.schedules = schedules
    try:
        yield
    finally:
        schedules.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent-harness",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/me", response_model=AuthInfo, dependencies=[Depends(require_auth)])
    async def me() -> AuthInfo:
        return AuthInfo()

    app.include_router(projects_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(schedules_routes.router)
    app.include_router(allowlist_routes.router)
    app.include_router(tasks_routes.router)
    app.include_router(outcomes_routes.router)
    app.include_router(stream_routes.router)
    app.include_router(stream_routes.schema_router)

    dist = _web_dist_dir()
    if dist is not None:
        app.mount("/", _SpaStaticFiles(directory=str(dist), html=True), name="frontend")

    return app


class _SpaStaticFiles(StaticFiles):
    """Static mount that falls back to index.html for unknown paths.

    Real asset requests (`/assets/foo.js`) still hit their files; client-side
    routes like `/auth` or `/jobs/:id` get the SPA shell so React Router can
    render them.
    """

    async def get_response(self, path, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


app = create_app()
