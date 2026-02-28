"""
FastAPI application factory.

Serves listing data with scores from the SQLite database
for the AptHunt frontend.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import listings


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="AptHunt API",
        version="1.0.0",
        description="Hyperlocal NYC rental scoring API",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(listings.router, prefix="/api")

    return app


app = create_app()
