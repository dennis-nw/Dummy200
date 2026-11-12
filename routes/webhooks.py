"""Relays Still200 incident webhooks to incident.io as alerts.

Still200 POSTs an event when an incident starts and when it resolves:

    {
      "version": "1",
      "id": "incident.started/<incident id>",
      "type": "incident.started" | "incident.resolved",
      "test": false,
      "data": {"incident": {
        "id", "monitor_id", "monitor_name", "error_message",
        "started_at", "resolved_at", "duration_seconds",
        "context": {"monitor_type": "http" | "scheduled_job", ...type-specific fields}
      }}
    }

Each event becomes an incident.io HTTP alert event. Both events of one incident
share the Still200 incident id as `deduplication_key`, so `incident.started`
opens an alert (`firing`) and `incident.resolved` closes that same alert.
"""

import hashlib
import hmac
import logging
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from config import (
    INCIDENT_IO_ALERT_SOURCE_ID,
    INCIDENT_IO_TOKEN,
    STILL200_WEBHOOK_SECRET,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

INCIDENT_IO_URL = "https://api.incident.io/v2/alert_events/http/{source_id}"

ALERT_STATUS = {"incident.started": "firing", "incident.resolved": "resolved"}

# Reject signatures whose timestamp is further than this from now (either way),
# so a captured request can't be replayed later.
SIGNATURE_TOLERANCE_SECONDS = 300


class InvalidSignature(Exception):
    pass


def verify_signature(
    raw_body: bytes, header: str | None, secret: str, now: float | None = None
) -> None:
    """Verify a `Still200-Webhook-Signature` header, raising InvalidSignature if
    it doesn't check out. See https://docs.still200.com/webhooks/signatures.

    The header looks like `t=<unix timestamp>,v1=<hex signature>`. The signature
    is HMAC-SHA256, keyed with the full secret (including its `s200_whsec_`
    prefix), over `<timestamp>.<raw body>`. Still200's docs say to accept a match
    against *any* `v1` value, so more than one is allowed (e.g. during secret
    rotation).
    """
    if not header:
        raise InvalidSignature("missing signature header")

    timestamp: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1" and value:
            signatures.append(value)
    if not timestamp or not timestamp.isdigit() or not signatures:
        raise InvalidSignature("malformed signature header")

    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        raise InvalidSignature("stale signature timestamp")

    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(sig, expected) for sig in signatures):
        raise InvalidSignature("signature mismatch")


class Incident(BaseModel):
    id: str
    monitor_id: str
    monitor_name: str
    # Shape varies by monitor_type, so keep it as a plain dict.
    context: dict[str, Any]
    error_message: str | None = None
    started_at: str
    resolved_at: str | None = None
    duration_seconds: float | None = None


class IncidentData(BaseModel):
    incident: Incident


class Still200Event(BaseModel):
    version: str
    id: str
    created_at: str
    test: bool
    type: str
    data: IncidentData


def to_alert_event(event: Still200Event) -> dict:
    """Translate a Still200 incident event into an incident.io alert event."""
    incident = event.data.incident
    error = incident.error_message or "No error message"
    context_lines = "\n".join(f"- **{k}**: {v}" for k, v in incident.context.items())
    return {
        "deduplication_key": incident.id,
        "status": ALERT_STATUS[event.type],
        # Error messages can be multi-line (e.g. httpx's "For more information..."),
        # so the title only takes the first line.
        "title": f"{incident.monitor_name}: {error.splitlines()[0]}",
        "description": f"{error}\n\n{context_lines}",
        "metadata": {
            "still200_event_id": event.id,
            "monitor_id": incident.monitor_id,
            "monitor_name": incident.monitor_name,
            "incident_started_at": incident.started_at,
            "incident_resolved_at": incident.resolved_at,
            "incident_duration_seconds": incident.duration_seconds,
            **incident.context,
        },
    }

@router.post("/still200", status_code=202)
async def still200(
    request: Request,
    signature: Annotated[str | None, Header(alias="Still200-Webhook-Signature")] = None,
) -> dict:
    """Receive a Still200 incident event and forward it to incident.io.

    The signature is checked against the raw body before anything is parsed:
    a missing, malformed, stale or wrong signature gets 401. If no webhook
    secret is configured, every request is refused with 503 — the endpoint
    never accepts unsigned events.

    Events that shouldn't create alerts are acknowledged with 202 but not
    forwarded, so Still200 doesn't retry them:
      - unknown event types
      - test events (a test `incident.started` would open an alert that no
        `incident.resolved` ever closes)
      - any event while incident.io isn't configured (dry run)

    If incident.io rejects the event or can't be reached, responds 502 so
    Still200 sees the delivery as failed.
    """
    if not STILL200_WEBHOOK_SECRET:
        logger.error("STILL200_WEBHOOK_SECRET is not set; refusing webhook")
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    raw_body = await request.body()
    try:
        verify_signature(raw_body, signature, STILL200_WEBHOOK_SECRET)
    except InvalidSignature as exc:
        logger.warning("Rejected Still200 webhook: %s", exc)
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        event = Still200Event.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())

    if event.type not in ALERT_STATUS:
        logger.info("Ignoring Still200 event %s of type %s", event.id, event.type)
        return {"forwarded": False, "reason": f"unhandled event type {event.type!r}"}

    alert = to_alert_event(event)

    if event.test:
        logger.info("Not forwarding Still200 test event %s: %s", event.id, alert)
        return {"forwarded": False, "reason": "test event", "alert": alert}

    if not (INCIDENT_IO_ALERT_SOURCE_ID and INCIDENT_IO_TOKEN):
        logger.warning(
            "incident.io not configured, dry run for %s: %s", event.id, alert
        )
        return {
            "forwarded": False,
            "reason": "incident.io not configured",
            "alert": alert,
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                INCIDENT_IO_URL.format(source_id=INCIDENT_IO_ALERT_SOURCE_ID),
                headers={"Authorization": f"Bearer {INCIDENT_IO_TOKEN}"},
                json=alert,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error(
            "Failed to forward Still200 event %s to incident.io: %s", event.id, exc
        )
        raise HTTPException(status_code=502, detail="failed to forward to incident.io")

    logger.info("Forwarded Still200 event %s to incident.io", event.id)
    return {"forwarded": True, "alert": alert}
