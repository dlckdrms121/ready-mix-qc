from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import router, shutdown_executor
from core.utils import ensure_dirs, setup_logging


def _load_cors_origins() -> list[str]:
    raw = os.getenv("SLUMPGUARD_CORS_ORIGINS", "").strip()
    if raw:
        if raw == "*":
            return ["*"]
        return [v.strip() for v in raw.split(",") if v.strip()]
    # Default: local dev + current GitHub Pages origin used by this project.
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://dlckdrms121.github.io",
    ]


def create_app() -> FastAPI:
    ensure_dirs()
    setup_logging()

    app = FastAPI(title="SlumpGuard Quality Analyzer", version="1.0.0")
    cors_origins = _load_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    root = Path(__file__).resolve().parents[1]
    app.mount("/static", StaticFiles(directory=str(root / "app" / "static")), name="static")
    app.mount("/data", StaticFiles(directory=str(root / "data")), name="data")

    @app.on_event("shutdown")
    def _on_shutdown() -> None:
        shutdown_executor()

    return app


app = create_app()
