"""
FastAPI application factory.

Serves listing data with scores from the SQLite database
for the AptHunt frontend.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import listings
from api import photos


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="AptHunt API",
        version="1.0.0",
        description="Hyperlocal NYC rental scoring API",
    )

    # Allow CORS from local dev and any deployed frontend origin(s)
    allowed_origins = ["http://localhost:3000"]
    extra = os.environ.get("CORS_ORIGINS", "")
    if extra:
        allowed_origins += [o.strip() for o in extra.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(listings.router, prefix="/api")
    app.include_router(photos.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
