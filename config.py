"""Settings shared across route modules."""

import os

SERVICE_NAME = "Dummy200"

# Signing secret for Still200 webhooks (starts with `s200_whsec_`). Required:
# without it, /webhooks/still200 refuses every request.
STILL200_WEBHOOK_SECRET = os.getenv("STILL200_WEBHOOK_SECRET")

# incident.io HTTP alert source (Alerts -> Sources -> HTTP). When either is unset,
# /webhooks/still200 runs in dry-run mode: it translates events but doesn't send them.
INCIDENT_IO_ALERT_SOURCE_ID = os.getenv("INCIDENT_IO_ALERT_SOURCE_ID")
INCIDENT_IO_TOKEN = os.getenv("INCIDENT_IO_TOKEN")
