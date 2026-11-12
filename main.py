"""Dummy API for testing Still200.

This module only assembles the app. Each group of endpoints lives in its own
module under `routes/`:

    routes/health.py    -> /health/*    health-check scenarios Still200 polls
    routes/webhooks.py  -> /webhooks/*  Still200 incident webhooks, relayed to incident.io
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute

from config import SERVICE_NAME
from routes import health, webhooks

app = FastAPI(
    title="Dummy200", description="Dummy endpoints for testing Still200", redoc_url=None
)
app.include_router(health.router)
app.include_router(webhooks.router)


@app.get("/")
async def root() -> dict:
    """Index listing the available scenarios, derived from the registered routes."""
    return {
        "service": SERVICE_NAME,
        "scenarios": [
            route.path for route in health.router.routes if isinstance(route, APIRoute)
        ],
    }
