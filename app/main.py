from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router, shutdown_executor
from core.utils import ensure_dirs, setup_logging


def create_app() -> FastAPI:
    ensure_dirs()
    setup_logging()

    app = FastAPI(title="SlumpGuard Quality Analyzer", version="1.0.0")
    app.include_router(router)

    root = Path(__file__).resolve().parents[1]
    app.mount("/static", StaticFiles(directory=str(root / "app" / "static")), name="static")
    app.mount("/data", StaticFiles(directory=str(root / "data")), name="data")

    @app.on_event("shutdown")
    def _on_shutdown() -> None:
        shutdown_executor()

    return app


app = create_app()
