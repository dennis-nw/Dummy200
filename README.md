# Dummy200

A small FastAPI app that fakes health endpoints for testing [Still200](https://dennis-nw.github.io/still200-integration-guide/), an API uptime monitor. Each route simulates a scenario and returns Still200's health-check JSON (`200` + `service_name` + `checks`) — no real dependencies.

Each check reports only `latency_ms` and an optional `error`; Still200 derives the status itself — `error` ⇒ unhealthy, high latency (no error) ⇒ degraded, otherwise healthy.

## Routes

| Route | Simulates |
|---|---|
| `GET /health/ping` | bare `200`, no `checks` (simplest integration) |
| `GET /health/healthy` | all dependencies green |
| `GET /health/degraded` | elevated latency, no errors (derives degraded) |
| `GET /health/unhealthy` | a hard dependency failure |
| `GET /health/slow` | slow but responds within the poll timeout |
| `GET /health/timeout` | never responds in time (trips Still200's fixed timeout) |
| `GET /health/flaky` | randomly healthy or unhealthy per request |
| `GET /health/error` | responds `503` instead of `200` |
| `GET /health/crash` | unhandled exception → `500`, non-JSON body |
| `GET /health/malformed` | `200` but body is invalid JSON |

`GET /` lists the available scenarios.

## Webhooks → incident.io

`POST /webhooks/still200` receives Still200's incident webhooks and relays them to an [incident.io HTTP alert source](https://docs.incident.io/api-reference). `incident.started` opens an alert (`firing`) and `incident.resolved` closes it (`resolved`). Both use the Still200 incident id as the `deduplication_key`, so they hit the same alert.

Every request must carry a valid [`Still200-Webhook-Signature`](https://docs.still200.com/webhooks/signatures) header (HMAC-SHA256 over `<timestamp>.<raw body>`, with a timestamp within 5 minutes). Missing, malformed, stale or wrong signatures get `401`.

Configure it with environment variables:

| Variable | Value |
|---|---|
| `STILL200_WEBHOOK_SECRET` | the webhook's signing secret (`s200_whsec_…`). **Required:** without it every request gets `503` |
| `INCIDENT_IO_ALERT_SOURCE_ID` | the alert source config id from the source's URL |
| `INCIDENT_IO_TOKEN` | the source's secret token |

When either incident.io variable is unset, the endpoint runs as a dry run: it returns the translated alert without sending it. Test events (`"test": true`) and unknown event types are also acknowledged with `202` and not forwarded. If incident.io rejects an event or can't be reached, the endpoint responds `502`.

## Layout

```
main.py             # builds the app and mounts the routers
config.py           # shared settings (service name, incident.io credentials)
routes/health.py    # /health/* scenarios
routes/webhooks.py  # /webhooks/* Still200 → incident.io relay
```

New groups of endpoints get their own module in `routes/` and one `include_router` line in `main.py`.

## Run

```sh
uv run fastapi dev   # serves on http://127.0.0.1:8000
```

Point Still200 at any `/health/*` route.
