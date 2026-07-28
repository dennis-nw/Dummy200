"""Dummy API for testing Still200's uptime monitoring.

Still200 polls a GET health endpoint, always expects an HTTP 200, and reads each
dependency's condition from the JSON body:

    {
      "service_name": "string",
      "checks": {
        "<dependency>": {
          "latency_ms": number,
          "error": "string (optional)"
        }
      }
    }

Each check reports only facts — how long the call took and whether it errored.
Still200 derives the healthy/degraded/unhealthy status itself:

    no error, low latency   -> healthy
    no error, high latency  -> degraded
    error present           -> unhealthy

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


def check(latency_ms: float, error: str | None = None) -> dict:
    """Build a single dependency check, omitting `error` when there isn't one.

    Still200 derives status from these fields: an `error` reads as unhealthy, a
    high `latency_ms` with no error reads as degraded, otherwise healthy.
    """
    result: dict = {"latency_ms": round(latency_ms, 1)}
    if error is not None:
        result["error"] = error
    return result


def health(checks: dict | None) -> dict:
    """Wrap dependency checks in the envelope Still200 expects."""
    if checks is not None:
        return {"service_name": SERVICE_NAME, "checks": checks}
    return {"service_name": SERVICE_NAME}


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
    `checks` to inspect, Still200 has only the 200 status to go on, which reads as up.
    """
    return health(checks=None)


@app.get("/health/healthy")
async def healthy() -> dict:
    """Everything green — every dependency reachable and fast."""
    return health(
        {
            "database": check(12.4),
            "cache": check(1.8),
            "payment_gateway": check(88.0),
        }
    )


@app.get("/health/degraded")
async def degraded() -> dict:
    """Reachable but slow — elevated latency on two dependencies, no errors.

    Degraded is now purely a latency signal (an error would read as unhealthy),
    so these latencies sit clearly above the ~500ms guideline.
    """
    return health(
        {
            "database": check(15.2),
            "cache": check(620.5),
            "payment_gateway": check(1280.0),
        }
    )


@app.get("/health/unhealthy")
async def unhealthy() -> dict:
    """A hard dependency failure — Still200 should classify this as unhealthy."""
    return health(
        {
            "database": check(5001.0, "connection refused (timeout after 5s)"),
            "cache": check(2.1),
            "payment_gateway": check(91.3),
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
    await asyncio.sleep(1.5)
    return health(
        {
            "database": check(14.0),
            "cache": check(2.4),
            "report_builder": check(130.0),
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
    return health({"database": check(14.0)})  # never actually reached


FLAKY_DEPENDENCIES = ("database", "cache", "payment_gateway", "message_queue")

FLAKY_ERRORS = (
    "connection reset by peer",
    "connection refused (timeout after 5s)",
    "connection pool exhausted",
    "read timeout after 3000ms",
    "TLS handshake failed",
    "DNS resolution failed",
    "unexpected EOF from upstream",
    "503 Service Unavailable from upstream",
)


@app.get("/health/flaky")
async def flaky() -> dict:
    """Randomly healthy or unhealthy per request, with a different failure each time.

    Good for exercising Still200's consecutive-failure threshold — a single blip
    shouldn't alert, but a run of failures should. On failure, a random dependency
    reports a random error, so no two failures look alike.
    """
    checks = {
        name: check(round(random.uniform(1.0, 20.0), 1)) for name in FLAKY_DEPENDENCIES
    }
    if random.random() < 0.5:
        return health(checks)

    failed = random.choice(FLAKY_DEPENDENCIES)
    checks[failed] = check(
        round(random.uniform(1000.0, 5000.0), 1), random.choice(FLAKY_ERRORS)
    )
    return health(checks)


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
