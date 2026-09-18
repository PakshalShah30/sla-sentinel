# SLA Sentinel

An integration SLA monitor built from the perspective of the analyst who has
to escalate a breach with evidence, not just a gut feeling that "the
payments API felt slow yesterday."

Author: [Pakshal Shah](https://pakshalshah.com)

## The problem

Most teams track integration SLAs (uptime %, p95 response time, error rate)
in one of two ways:

1. **A spreadsheet someone updates after an incident**, reconstructed from
   memory, support tickets, and whatever logs didn't get rotated out yet.
2. **A general-purpose APM dashboard** that shows raw request metrics but
   has no concept of "this integration has a 99.9% uptime SLA with our
   contractual partner and it just got breached three hours ago."

Neither gives you what you actually need when a vendor's integration
degrades: a timestamped record of *exactly when* the breach started, *by
how much* the SLA was missed, and probe-level evidence you can attach to an
escalation email. SLA Sentinel is a small, self-hosted system that closes
that gap — it treats SLA compliance as a first-class, continuously computed
number instead of an after-the-fact investigation.

## Architecture

```
config.yaml ──► Probe engine ──► SQLite ──► Metrics (rolling window)
                (async, per-                    │
                 integration                     ▼
                 interval)                 Breach detection ──► Alert
                                            (vs. SLA thresholds,     (log or
                                             cooldown-limited)        Slack)
                                                  │
                                                  ▼
                                          FastAPI + dashboard
                                          (/api/*, single HTML page)
```

- **`app/config.py`** — loads `config.yaml`: integrations, probe settings,
  SLA thresholds, alert mode. No secrets in YAML; the Slack webhook URL is
  read from an environment variable at dispatch time, never stored.
- **`app/prober.py`** — one asyncio task per integration, looping on its own
  `interval_seconds`. Timeouts and connection errors are recorded as failed
  probes, not exceptions — a probe that can't reach its target is exactly
  the kind of event this system exists to catch.
- **`app/db.py`** — SQLite storage: `probes`, `breaches`, `breach_state`
  (cooldown bookkeeping). Plain SQL, no ORM.
- **`app/metrics.py`** — pure functions: uptime %, p50/p95/p99 latency
  (linear-interpolation percentile, same convention as numpy's default),
  error rate, over a rolling window. No I/O, fully unit tested.
- **`app/breaches.py`** — compares metrics to thresholds, records which
  metric breached and by how much, and enforces a cooldown so a sustained
  outage doesn't generate a fresh alert every probe cycle.
- **`app/service.py`** — glues config + db + metrics + breach detection
  together; used identically by the API, the live prober, and the demo
  seed script.
- **`app/main.py`** — FastAPI app: JSON API + the dashboard.
- **`static/dashboard.html`** — a single self-contained HTML page (inline
  CSS, Chart.js from a CDN) — no build step, no frontend framework.
- **`mock_target/app.py`** — a small local FastAPI app standing in for four
  real integrations, so the prober has something real to hit without any
  network access or paid services.
- **`scripts/seed_demo.py`** — generates several days of realistic
  synthetic probe history (including a genuine latency degradation and a
  genuine outage) and replays the exact same breach-detection code the
  live prober uses, so the dashboard is populated the moment you clone the
  repo.

## Quick start (3 minutes)

```bash
git clone <this-repo>
cd sla-sentinel
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt

python scripts/seed_demo.py        # populates data/sla_sentinel.db with 5 days of history

uvicorn app.main:app --reload      # starts the API + dashboard
```

Open **http://127.0.0.1:8000/** — you'll see four integrations, one of them
green, one amber (an ongoing latency degradation), and one red (a real
outage inside the last 24 hours), plus a breach table with the evidence.

By default the app also starts a live background prober against
`config.yaml`'s URLs (`http://localhost:9100/...`, served by the mock
target). Those requests will fail with connection errors until you also
run the mock target — which is fine, and exactly the failure-handling
behavior the prober is designed for — but if you want to see live,
successful probes:

```bash
uvicorn mock_target.app:app --port 9100   # in a second terminal
```

To disable the background prober entirely (pure demo browsing, no live
network calls):

```bash
SLA_SENTINEL_ENABLE_PROBER=false uvicorn app.main:app --reload
```

## Real output

The JSON below is the **actual response** from `GET /api/metrics?window=24h`
after running `python scripts/seed_demo.py` — not a mock-up. The
`latency_series` arrays (1,440 points each) were trimmed to the last 3
points for readability; everything else is unedited.

```json
{
  "window": "24h",
  "window_hours": 24.0,
  "integrations": [
    {
      "name": "payments-api",
      "url": "http://localhost:9100/payments/health",
      "method": "GET",
      "expected_status": 200,
      "window_hours": 24.0,
      "sla": { "uptime_pct": 99.5, "p95_latency_ms": 400.0, "error_rate_pct": 1.0 },
      "metrics": {
        "total_probes": 1440,
        "ok_probes": 1437,
        "uptime_pct": 99.79,
        "error_rate_pct": 0.21,
        "p50_latency_ms": 90.0,
        "p95_latency_ms": 134.0,
        "p99_latency_ms": 152.0
      },
      "status": "green"
    },
    {
      "name": "shipping-api",
      "url": "http://localhost:9100/shipping/health",
      "method": "GET",
      "expected_status": 200,
      "window_hours": 24.0,
      "sla": { "uptime_pct": 99.0, "p95_latency_ms": 600.0, "error_rate_pct": 2.0 },
      "metrics": {
        "total_probes": 1440,
        "ok_probes": 1437,
        "uptime_pct": 99.79,
        "error_rate_pct": 0.21,
        "p50_latency_ms": 232.0,
        "p95_latency_ms": 1030.0,
        "p99_latency_ms": 1140.0
      },
      "status": "amber"
    },
    {
      "name": "inventory-api",
      "url": "http://localhost:9100/inventory/health",
      "method": "GET",
      "expected_status": 200,
      "window_hours": 24.0,
      "sla": { "uptime_pct": 99.9, "p95_latency_ms": 300.0, "error_rate_pct": 0.5 },
      "metrics": {
        "total_probes": 1440,
        "ok_probes": 1375,
        "uptime_pct": 95.49,
        "error_rate_pct": 4.51,
        "p50_latency_ms": 71.0,
        "p95_latency_ms": 104.0,
        "p99_latency_ms": 120.0
      },
      "status": "red"
    },
    {
      "name": "auth-api",
      "url": "http://localhost:9100/auth/health",
      "method": "GET",
      "expected_status": 200,
      "window_hours": 24.0,
      "sla": { "uptime_pct": 99.9, "p95_latency_ms": 250.0, "error_rate_pct": 0.5 },
      "metrics": {
        "total_probes": 1440,
        "ok_probes": 1439,
        "uptime_pct": 99.93,
        "error_rate_pct": 0.07,
        "p50_latency_ms": 45.0,
        "p95_latency_ms": 65.0,
        "p99_latency_ms": 72.0
      },
      "status": "green"
    }
  ]
}
```

And the corresponding `GET /api/breaches?limit=5` at the same moment —
this is the evidence an analyst would attach to an escalation:

```json
[
  {
    "id": 30,
    "integration": "inventory-api",
    "ts": "2026-09-18T05:10:21.003049+00:00",
    "metric": "error_rate_pct",
    "threshold": 0.5,
    "actual": 4.51,
    "diff": 4.01,
    "message": "inventory-api: error rate 4.51% exceeds the 0.50% SLA (over by 4.01 points)",
    "alerted": 1
  },
  {
    "id": 29,
    "integration": "shipping-api",
    "ts": "2026-09-18T05:10:21.003049+00:00",
    "metric": "p95_latency_ms",
    "threshold": 600.0,
    "actual": 1029.5,
    "diff": 429.5,
    "message": "shipping-api: p95 latency 1030ms exceeds the 600ms SLA (over by 430ms)",
    "alerted": 1
  },
  {
    "id": 28,
    "integration": "inventory-api",
    "ts": "2026-09-18T04:40:21.003049+00:00",
    "metric": "uptime_pct",
    "threshold": 99.9,
    "actual": 95.49,
    "diff": 4.41,
    "message": "inventory-api: uptime 95.49% is below the 99.90% SLA (short by 4.41 points, 1376/1441 probes ok)",
    "alerted": 1
  }
]
```

`inventory-api`'s story, visible directly in this data: a real ~70-minute
outage about 5.5 hours before this snapshot pulled its trailing-24h uptime
down to 95.49% against a 99.9% SLA — a 4.41-point miss, with the exact
probe counts (1,376/1,441 ok) to back it up.

## Config reference

All settings live in `config.yaml` (copy `config.example.yaml` to get
started — they're identical; the example exists purely as an explicit,
documented template).

| Key | Description | Default |
|---|---|---|
| `window_hours` | Rolling window (hours) for SLA metric calculations | `24` |
| `breach_cooldown_minutes` | Minimum minutes between repeat alerts for the same integration + metric | `60` |
| `alerts.mode` | `log` or `slack` | `log` |
| `alerts.slack_webhook_env` | Name of the env var holding the Slack webhook URL (never the URL itself) | `SLACK_WEBHOOK_URL` |
| `db_path` | SQLite file path (overridable with `SLA_SENTINEL_DB_PATH`) | `data/sla_sentinel.db` |
| `integrations[].name` | Integration display name | — |
| `integrations[].url` | Endpoint to probe | — |
| `integrations[].method` | HTTP method | `GET` |
| `integrations[].expected_status` | Status code that counts as success | `200` |
| `integrations[].timeout_seconds` | Per-request timeout | `5` |
| `integrations[].interval_seconds` | How often to probe | `60` |
| `integrations[].sla.uptime_pct` | Minimum acceptable uptime % | — |
| `integrations[].sla.p95_latency_ms` | Maximum acceptable p95 latency (ms) | — |
| `integrations[].sla.error_rate_pct` | Maximum acceptable error rate % | — |
| `integrations[].enabled` | Set `false` to keep an integration configured but not probed | `true` |

Breach boundary convention: a metric **exactly equal** to its threshold is
not a breach. Uptime must fall strictly below the minimum; latency and
error rate must rise strictly above the maximum. (Covered by
`tests/test_breaches.py`.)

### Slack alerts (optional)

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

Then set `alerts.mode: slack` in `config.yaml`. If `mode` is `slack` but
the env var isn't set, SLA Sentinel logs a warning and falls back to
log-only rather than silently failing.

## Running the tests

```bash
pip install -r requirements.txt
pytest -v
```

20 tests, covering:

- **Metric math** (`tests/test_metrics.py`) — including a hand-checked
  percentile example: for `values = [10, 20, ..., 100]`, p95 via linear
  interpolation is `rank = 0.95 * 9 = 8.55` → between `values[8]=90` and
  `values[9]=100` → `90 + 0.55 * 10 = 95.5`, asserted directly against the
  implementation.
- **Breach detection at threshold boundaries** (`tests/test_breaches.py`)
  — a metric exactly at its threshold must not breach; one unit past it
  must.
- **Cooldown logic** (`tests/test_cooldown.py`) — a sustained breach must
  alert once, stay silent for the cooldown window even while still
  breaching, and alert again once the cooldown expires. Verified both as a
  pure function (`should_alert`) and end-to-end against a real SQLite
  database (`process_breaches`).

## What's here vs. what's left out (honest limitations)

**Done:**
- Async multi-integration probing with graceful timeout/connection-error
  handling
- Rolling-window uptime / p50 / p95 / p99 / error-rate computation
- Threshold-based breach detection with cooldown-limited alerting
- Log and Slack-webhook alert delivery
- A working dashboard with real charts and a breach history table
- A fully synthetic but realistic demo dataset, generated through the same
  code path that powers live monitoring
- 20 passing unit/integration tests

**Left out, on purpose, for a project this size:**
- **No authentication** on the API or dashboard. Fine for a single
  operator running this locally or on an internal network; not fine to
  expose publicly as-is.
- **No multi-user / multi-tenant support.** One SQLite file, one config
  file, one team.
- **No horizontal scaling.** The prober runs as asyncio tasks inside the
  same process as the API server. That's the right tradeoff for
  monitoring a handful of integrations; it would need to become a
  separate worker (and SQLite would need to become Postgres) well before
  you got to hundreds.
- **No PagerDuty / OpsGenie / email integrations** — just log and Slack.
  The alert dispatcher (`app/alerts.py`) is small and intentionally easy
  to extend.
- **No historical breach analytics** (MTTR, breach frequency trends over
  months) — the breaches table has everything needed to build this, it
  just isn't built yet.
- **No TLS/cert validation configuration** for probed endpoints beyond
  what `httpx` does by default.
- **SQLite, not a time-series database.** Entirely adequate at the probe
  volumes this is designed for (a handful of integrations probed every
  30-60 seconds); would need reconsideration at a much larger fleet.

## License

MIT — see [LICENSE](LICENSE).
