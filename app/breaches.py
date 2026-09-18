"""Breach detection: compare metrics to SLA thresholds and manage alerting.

Split into small, independently-testable pieces:

- `evaluate_breaches` is pure: given a Metrics snapshot and a set of
  thresholds, it returns which metrics are breached and by how much. No I/O.
- `should_alert` is pure: given the last time this exact breach alerted and
  the configured cooldown, decide whether to alert again now.
- `process_breaches` wires the two together with the database and the
  alert dispatcher, and is what the prober / seed script actually call.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app import db as dbmod
from app.alerts import dispatch_alert
from app.config import AlertConfig, SLAThresholds
from app.metrics import Metrics, iso, utcnow


@dataclass
class BreachResult:
    metric: str
    threshold: float
    actual: float
    diff: float
    message: str


def evaluate_breaches(metrics: Metrics, sla: SLAThresholds, integration_name: str) -> list[BreachResult]:
    """Pure comparison of metrics against thresholds.

    Boundary convention: a metric exactly equal to its threshold is NOT a
    breach. uptime must fall strictly below the minimum; latency and error
    rate must rise strictly above the maximum.
    """
    results: list[BreachResult] = []

    if metrics.uptime_pct < sla.uptime_pct:
        diff = sla.uptime_pct - metrics.uptime_pct
        results.append(
            BreachResult(
                metric="uptime_pct",
                threshold=sla.uptime_pct,
                actual=round(metrics.uptime_pct, 2),
                diff=round(diff, 2),
                message=(
                    f"{integration_name}: uptime {metrics.uptime_pct:.2f}% is below the "
                    f"{sla.uptime_pct:.2f}% SLA (short by {diff:.2f} points, "
                    f"{metrics.ok_probes}/{metrics.total_probes} probes ok)"
                ),
            )
        )

    if metrics.p95_latency_ms is not None and metrics.p95_latency_ms > sla.p95_latency_ms:
        diff = metrics.p95_latency_ms - sla.p95_latency_ms
        results.append(
            BreachResult(
                metric="p95_latency_ms",
                threshold=sla.p95_latency_ms,
                actual=round(metrics.p95_latency_ms, 1),
                diff=round(diff, 1),
                message=(
                    f"{integration_name}: p95 latency {metrics.p95_latency_ms:.0f}ms exceeds "
                    f"the {sla.p95_latency_ms:.0f}ms SLA (over by {diff:.0f}ms)"
                ),
            )
        )

    if metrics.error_rate_pct > sla.error_rate_pct:
        diff = metrics.error_rate_pct - sla.error_rate_pct
        results.append(
            BreachResult(
                metric="error_rate_pct",
                threshold=sla.error_rate_pct,
                actual=round(metrics.error_rate_pct, 2),
                diff=round(diff, 2),
                message=(
                    f"{integration_name}: error rate {metrics.error_rate_pct:.2f}% exceeds "
                    f"the {sla.error_rate_pct:.2f}% SLA (over by {diff:.2f} points)"
                ),
            )
        )

    return results


def should_alert(
    last_alert_ts: Optional[datetime],
    now: datetime,
    cooldown_minutes: float,
) -> bool:
    """True if enough time has passed since the last alert for this exact
    (integration, metric) pair, or if it has never alerted before."""
    if last_alert_ts is None:
        return True
    return now - last_alert_ts >= timedelta(minutes=cooldown_minutes)


def process_breaches(
    conn: sqlite3.Connection,
    *,
    integration_name: str,
    metrics: Metrics,
    sla: SLAThresholds,
    alert_config: AlertConfig,
    cooldown_minutes: float,
    now: Optional[datetime] = None,
) -> list[BreachResult]:
    """Evaluate breaches for one integration, record + alert respecting the
    cooldown, and return the list of breaches that were newly alerted this
    cycle (an empty list means either no breach or all breaches were still
    within their cooldown window)."""
    now = now or utcnow()
    breaches = evaluate_breaches(metrics, sla, integration_name)
    newly_alerted: list[BreachResult] = []

    for breach in breaches:
        last_ts_raw = dbmod.get_last_alert_ts(conn, integration_name, breach.metric)
        last_ts = datetime.fromisoformat(last_ts_raw) if last_ts_raw else None
        if not should_alert(last_ts, now, cooldown_minutes):
            continue  # still in cooldown: suppress duplicate alert + record

        dbmod.insert_breach(
            conn,
            integration=integration_name,
            ts=iso(now),
            metric=breach.metric,
            threshold=breach.threshold,
            actual=breach.actual,
            diff=breach.diff,
            message=breach.message,
            alerted=True,
        )
        dbmod.set_last_alert_ts(conn, integration_name, breach.metric, iso(now))
        dispatch_alert(alert_config, title=f"SLA breach: {integration_name}", message=breach.message)
        newly_alerted.append(breach)

    return newly_alerted
