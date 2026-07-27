"""Dummy API for testing Still200's uptime monitoring.

Still200 polls a GET health endpoint, always expects an HTTP 200, and reads the
real status from the JSON body:

    {
      "service_name": "string",
      "checks": {
        "<dependency>": {
          "status": "healthy|degraded|unhealthy",
          "latency_ms": number,
          "error": "string (optional)"
        }
      }
    }

Each route below simulates a different scenario so you can point Still200 at it
and verify how it classifies and alerts. No real DB/Redis — every dependency is
faked.
"""

import asyncio
import random

from fastapi import FastAPI, HTTPException, Response

SERVICE_NAME = "Dummy200"

app = FastAPI(
    title="Dummy200", description="Dummy endpoints for testing Still200", redoc_url=None
)


def check(status: str, latency_ms: float, error: str | None = None) -> dict:
    """Build a single dependency check, omitting `error` when there isn't one."""
    result: dict = {"status": status, "latency_ms": round(latency_ms, 1)}
    if error is not None:
        result["error"] = error
    return result


def health(checks: dict) -> dict:
    """Wrap dependency checks in the envelope Still200 expects."""
    return {"service_name": SERVICE_NAME, "checks": checks}


@app.get("/")
async def root() -> dict:
    """Index listing the available scenarios."""
    return {
        "service": SERVICE_NAME,
        "scenarios": [
            "/health/ping",
            "/health/healthy",
            "/health/degraded",
            "/health/unhealthy",
            "/health/slow",
            "/health/timeout",
            "/health/flaky",
            "/health/error",
            "/health/crash",
            "/health/malformed",
        ],
    }


@app.get("/health/ping")
async def ping() -> dict:
    """Simplest possible integration — a bare 200 with no `checks`.

    Represents a user who just points Still200 at an existing endpoint. With no
    body to inspect, Still200 has only the 200 status to go on, which reads as up.
    """
    return {"status": "ok"}


@app.get("/health/healthy")
async def healthy() -> dict:
    """Everything green — every dependency reachable and fast."""
    return health(
        {
            "database": check("healthy", 12.4),
            "cache": check("healthy", 1.8),
            "payment_gateway": check("healthy", 88.0),
        }
    )


@app.get("/health/degraded")
async def degraded() -> dict:
    """Reachable but unwell — slow cache and a soft error from an upstream."""
    return health(
        {
            "database": check("healthy", 15.2),
            "cache": check("degraded", 620.5),
            "payment_gateway": check(
                "degraded", 430.0, "intermittent 5xx from upstream (soft errors)"
            ),
        }
    )


@app.get("/health/unhealthy")
async def unhealthy() -> dict:
    """A hard dependency failure — Still200 should classify this as unhealthy."""
    return health(
        {
            "database": check(
                "unhealthy", 5001.0, "connection refused (timeout after 5s)"
            ),
            "cache": check("healthy", 2.1),
            "payment_gateway": check("healthy", 91.3),
        }
    )


@app.get("/health/slow")
async def slow() -> dict:
    """Sleeps past the 500ms guideline but still well under the poll timeout,
    then responds healthy.

    Tests response-time recording — the endpoint is slow but answers in time, so
    Still200 still gets a `checks` body. Contrast with /health/timeout, which
    never responds in time.
    """
    delay_s = 1.5
    await asyncio.sleep(delay_s)
    return health(
        {
            "database": check("healthy", 14.0),
            "cache": check("healthy", 2.4),
            "report_builder": check("healthy", delay_s * 1000),
        }
    )


@app.get("/health/timeout")
async def timeout() -> dict:
    """Sleeps past Still200's PING_TIMEOUT_SECONDS (default 5s) so the poll is
    aborted before any response is sent.

    Exercises Still200's own request-timeout handling — no `checks` body is ever
    received, so it must classify the target as down on its own.
    """
    await asyncio.sleep(10)
    return health({"database": check("healthy", 14.0)})  # never actually reached


@app.get("/health/flaky")
async def flaky() -> dict:
    """Randomly healthy or unhealthy per request.

    Good for exercising Still200's consecutive-failure threshold — a single blip
    shouldn't alert, but a run of failures should.
    """
    if random.random() < 0.5:
        return health(
            {
                "database": check("healthy", 13.7),
                "cache": check("healthy", 2.0),
            }
        )
    return health(
        {
            "database": check("unhealthy", 3000.0, "connection reset by peer"),
            "cache": check("healthy", 2.0),
        }
    )


@app.get("/health/error")
async def error() -> dict:
    """Responds with HTTP 503 instead of 200.

    Still200 treats any non-200 status as a failure regardless of the body (note:
    this differs from its integration guide, which claims status codes are
    ignored). Complements /health/unhealthy, which reports failure via the body.
    """
    raise HTTPException(status_code=503, detail="service unavailable")


@app.get("/health/crash")
async def crash() -> dict:
    """Raises an unhandled exception, simulating a bug in the health check itself.

    FastAPI turns this into a 500 with a plain-text "Internal Server Error" body —
    so unlike /health/error, there's no parseable JSON at all. Tests how Still200
    handles a crashed endpoint that returns neither a valid status nor a body.
    """
    raise RuntimeError("unexpected error while collecting checks")


@app.get("/health/malformed")
async def malformed() -> Response:
    """Returns HTTP 200 and Content-Type application/json, but a body that isn't
    valid JSON (truncated mid-object).

    Tests how the pinger handles a parse failure on an otherwise-successful
    response — e.g. does it treat unparseable JSON as down, or crash on it?
    """
    return Response(
        content='{"service_name": "Dummy200", "checks": {',
        media_type="application/json",
    )
