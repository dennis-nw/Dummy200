# Dummy200

A small FastAPI app that fakes health endpoints for testing [Still200](https://dennis-nw.github.io/still200-integration-guide/), an API uptime monitor. Each route simulates a scenario and returns Still200's health-check JSON (`200` + `service_name` + `checks`) — no real dependencies.

## Routes

| Route | Simulates |
|---|---|
| `GET /health/healthy` | all dependencies green |
| `GET /health/degraded` | slow cache + intermittent soft errors |
| `GET /health/unhealthy` | a hard dependency failure |
| `GET /health/slow` | slow but responds within the poll timeout |
| `GET /health/timeout` | never responds in time (trips Still200's fixed timeout) |
| `GET /health/flaky` | randomly healthy or unhealthy per request |
| `GET /health/error` | responds `503` instead of `200` |
| `GET /health/crash` | unhandled exception → `500`, non-JSON body |

`GET /` lists the available scenarios.

## Run

```sh
uv run fastapi dev   # serves on http://127.0.0.1:8000
```

Point Still200 at any `/health/*` route.
