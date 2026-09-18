"""Glue layer: wires config + db + metrics + breach detection together.

Used by the FastAPI routes, the background prober, and the demo seed
script so all three exercise exactly the same logic.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app import db as dbmod
from app.breaches import evaluate_breaches, process_breaches
from app.config import AppConfig, Integration
from app.metrics import Metrics, ProbeRecord, compute_metrics, iso, utcnow, window_start


def get_metrics(
    conn: sqlite3.Connection,
    integration_name: str,
    window_hours: float,
    now: Optional[datetime] = None,
) -> Metrics:
    """Metrics over (now - window_hours, now]. `now` defaults to the real
    current time, but callers replaying history (e.g. the demo seed script)
    can pass a past timestamp to get an accurate point-in-time snapshot
    that doesn't peek at probes recorded "after" that moment."""
    now = now or utcnow()
    since = window_start(window_hours, now)
    rows = dbmod.fetch_probes(conn, integration_name, iso(since), iso(now))
    records = [ProbeRecord(ok=bool(r["ok"]), latency_ms=r["latency_ms"]) for r in rows]
    return compute_metrics(records)


def status_for(metrics: Metrics, integration: Integration) -> str:
    """green / amber / red summary used by the dashboard.

    red: the availability SLA itself is breached (an outage, the most
    severe class of breach). amber: latency or error-rate SLA breached but
    the integration is still up. green: everything within SLA.
    """
    breaches = evaluate_breaches(metrics, integration.sla, integration.name)
    if any(b.metric == "uptime_pct" for b in breaches):
        return "red"
    if breaches:
        return "amber"
    return "green"


def evaluate_integration(
    conn: sqlite3.Connection,
    config: AppConfig,
    integration: Integration,
    now: Optional[datetime] = None,
) -> Metrics:
    """Compute current-window metrics for one integration and run breach
    detection + (cooldown-gated) alerting against them. Returns the metrics
    so callers (e.g. the API) can reuse them without recomputing."""
    now = now or utcnow()
    metrics = get_metrics(conn, integration.name, config.window_hours, now)
    process_breaches(
        conn,
        integration_name=integration.name,
        metrics=metrics,
        sla=integration.sla,
        alert_config=config.alerts,
        cooldown_minutes=config.breach_cooldown_minutes,
        now=now,
    )
    return metrics


def integration_summary(
    conn: sqlite3.Connection,
    config: AppConfig,
    integration: Integration,
    window_hours: Optional[float] = None,
) -> dict:
    window_hours = config.window_hours if window_hours is None else window_hours
    metrics = get_metrics(conn, integration.name, window_hours)
    return {
        "name": integration.name,
        "url": integration.url,
        "method": integration.method,
        "expected_status": integration.expected_status,
        "window_hours": window_hours,
        "sla": {
            "uptime_pct": integration.sla.uptime_pct,
            "p95_latency_ms": integration.sla.p95_latency_ms,
            "error_rate_pct": integration.sla.error_rate_pct,
        },
        "metrics": metrics.rounded(),
        "status": status_for(metrics, integration),
    }


def parse_window(window: str, default_hours: float) -> float:
    """Parse a query-string window like '24h', '7d', '90m' into hours.
    Falls back to `default_hours` for an empty or unparseable value."""
    if not window:
        return default_hours
    window = window.strip().lower()
    try:
        if window.endswith("h"):
            return float(window[:-1])
        if window.endswith("d"):
            return float(window[:-1]) * 24.0
        if window.endswith("m"):
            return float(window[:-1]) / 60.0
        return float(window)
    except ValueError:
        return default_hours


def latency_series(conn: sqlite3.Connection, integration_name: str, window_hours: float) -> list[dict]:
    """Timestamp/latency pairs for the sparkline chart, oldest first.
    Probes with no latency sample (timeouts / connection errors) are
    represented with latency_ms = null so the chart can show a gap."""
    since = window_start(window_hours)
    rows = dbmod.fetch_probes(conn, integration_name, iso(since))
    return [{"ts": r["ts"], "latency_ms": r["latency_ms"], "ok": bool(r["ok"])} for r in rows]
