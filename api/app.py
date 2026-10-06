"""
FastAPI application factory.

Serves listing data with scores from the SQLite database
for the AptHunt frontend.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import listings
from apthunt.db import DB_PATH, ensure_database_ready, inspect_database

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fail fast when the configured production database is missing or invalid."""
    require_listings = os.environ.get("APTHUNT_REQUIRE_LISTINGS", "true").lower() != "false"
    db_summary = ensure_database_ready(DB_PATH, require_listings=require_listings)
    app.state.db_summary = db_summary
    log.info(
        "Database ready: path=%s total=%s api_visible=%s",
        db_summary["path"],
        db_summary["total_listings"],
        db_summary["api_visible_listings"],
    )
    yield


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="AptHunt API",
        version="1.0.0",
        description="Hyperlocal NYC rental scoring API",
        lifespan=lifespan,
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

    @app.get("/api/health")
    def health():
        db_summary = inspect_database(DB_PATH)
        status_code = 200 if db_summary["api_visible_listings"] > 0 else 503
        status = "ok" if status_code == 200 else "error"
        return JSONResponse(
            status_code=status_code,
            content={
                "status": status,
                "database": db_summary,
            },
        )

    return app


app = create_app()
