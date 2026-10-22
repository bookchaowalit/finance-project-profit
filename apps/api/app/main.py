"""Application factory: stateless, settings from the environment."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.auth import TokenVerifier
from app.config import ConfigError, Settings
from app.db import make_engine
from app.errors import (
    BodyLimitMiddleware,
    RequestContextMiddleware,
    error_body,
    install_error_handlers,
)
from app.observability import init_sentry
from app.routes import router

log = logging.getLogger("profit_api")
API_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def head_revision() -> str:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if head is None:
        raise RuntimeError("no migration head")
    return head


def create_app(settings: Settings | None = None) -> FastAPI:
    try:
        cfg = settings or Settings.from_env()
    except ConfigError as exc:
        log.error("invalid_configuration reason=%s", exc)
        raise
    app = FastAPI(title="Project Profit API", version="0.1.0", docs_url=None, redoc_url=None)
    engine = make_engine(cfg)
    app.state.engine = engine
    app.state.session_factory = (
        sessionmaker(engine, expire_on_commit=False, autoflush=False) if engine else None
    )
    app.state.verifier = TokenVerifier(cfg.auth, cfg.auth_error)
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    def ready(request: Request) -> Any:
        factory = app.state.session_factory
        problem: str | None = None
        if cfg.auth is None:
            problem = "authentication is not configured"
        elif factory is None:
            problem = "database not configured"
        else:
            try:
                with factory() as session:
                    version = session.scalar(text("SELECT version_num FROM alembic_version"))
                if version != head_revision():
                    problem = "database schema is not at the expected migration"
            except Exception as exc:
                log.error("not_ready type=%s", type(exc).__name__)
                problem = "database unavailable or schema missing"
        if problem:
            return JSONResponse(
                error_body("service_unavailable", problem, str(request.state.request_id)),
                status_code=503,
            )
        return {"status": "ready"}

    return app


def app_factory() -> FastAPI:
    """Uvicorn entrypoint: ``uvicorn app.main:app_factory --factory``."""
    init_sentry()  # no-op unless SENTRY_DSN is set
    return create_app()
