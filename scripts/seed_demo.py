#!/usr/bin/env python3
"""Populate the SQLite database with several days of realistic synthetic
probe history, so the dashboard is populated the moment you clone the repo
— no need to wait hours for the real prober to accumulate data.

Scenarios (four integrations, matching config.yaml / config.example.yaml):
  payments-api   - healthy the whole time
  shipping-api   - healthy, but with an ongoing latency degradation in the
                   last few hours that pushes p95 over its SLA
  inventory-api  - healthy, but had a real outage a few hours ago that
                   pulled 24h uptime below its SLA (a genuine, evidence-
                   backed breach an analyst could escalate)
  auth-api       - healthy the whole time

After generating the probe history, this script replays breach detection
at regular checkpoints across the trailing window using the exact same
`app.breaches` / `app.service` code the live prober uses, so the breaches
table and the cooldown behavior are produced by production logic, not
hand-written fixture rows.

Usage:
    python scripts/seed_demo.py [--days 5] [--config config.yaml]
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as dbmod  # noqa: E402
from app import service  # noqa: E402
from app.breaches import process_breaches  # noqa: E402
from app.config import AppConfig, Integration, load_config  # noqa: E402
from app.metrics import iso  # noqa: E402

random.seed(42)  # deterministic demo data


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def gen_latency(mean_ms: float, spread_ms: float, floor_ms: float = 5.0) -> float:
    return clamp(random.gauss(mean_ms, spread_ms), floor_ms, mean_ms + spread_ms * 6)


def generate_probes(
    integration: Integration,
    start: datetime,
    end: datetime,
    scenario,
) -> list[tuple]:
    """Returns a list of (integration, ts, status_code, latency_ms, ok, error)
    tuples ready for a bulk insert, one per configured probe interval."""
    rows = []
    step = timedelta(seconds=integration.interval_seconds)
    t = start
    while t <= end:
        status_code, latency_ms, ok, error = scenario(t, end)
        rows.append((integration.name, iso(t), status_code, latency_ms, 1 if ok else 0, error))
        t += step
    return rows


# --- Per-integration scenarios -------------------------------------------------


def payments_scenario(t: datetime, end: datetime):
    """Healthy: fast, reliable, a rare isolated blip."""
    if random.random() < 0.0015:
        return None, None, False, "timeout: read timed out"
    latency = gen_latency(mean_ms=90, spread_ms=25)
    return 200, round(latency, 1), True, None


def auth_scenario(t: datetime, end: datetime):
    """Healthy: very fast, very reliable. Failure rate is kept well below
    what its strict 99.9% SLA can absorb over a 24h window, so it reads as
    a genuinely healthy integration rather than one that flickers."""
    if random.random() < 0.00015:
        return None, None, False, "connection error: refused"
    latency = gen_latency(mean_ms=45, spread_ms=12)
    return 200, round(latency, 1), True, None


def shipping_scenario(t: datetime, end: datetime):
    """Healthy overall, but degrading: over the trailing 8 hours before
    `end`, latency climbs well past the 600ms p95 SLA. Still returns 200 -
    this is a performance problem, not an availability one."""
    degradation_start = end - timedelta(hours=8)
    if t >= degradation_start:
        # Ramp in: the first hour of the window is transitional, then it's
        # consistently slow.
        into_degradation = (t - degradation_start).total_seconds() / 3600.0
        if into_degradation < 1.0 and random.random() < (1.0 - into_degradation):
            latency = gen_latency(mean_ms=220, spread_ms=60)
        else:
            latency = gen_latency(mean_ms=850, spread_ms=180)
    else:
        latency = gen_latency(mean_ms=200, spread_ms=50)

    if random.random() < 0.002:
        return None, None, False, "timeout: read timed out"
    return 200, round(latency, 1), True, None


def inventory_scenario(t: datetime, end: datetime):
    """Healthy overall, but had a genuine ~70-minute outage a few hours ago
    that is still inside the trailing 24h SLA window: most probes during
    that window failed outright (timeouts / connection errors), which is
    enough to pull 24h uptime below the 99.9% SLA."""
    outage_start = end - timedelta(hours=5, minutes=30)
    outage_end = outage_start + timedelta(minutes=70)
    if outage_start <= t <= outage_end:
        if random.random() < 0.92:
            error = random.choice(
                ["connection error: refused", "timeout: read timed out", "unexpected status 503"]
            )
            status_code = 503 if "status" in error else None
            return status_code, None, False, error
        latency = gen_latency(mean_ms=400, spread_ms=100)
        return 200, round(latency, 1), True, None

    if random.random() < 0.0002:
        return None, None, False, "connection error: refused"
    latency = gen_latency(mean_ms=70, spread_ms=20)
    return 200, round(latency, 1), True, None


SCENARIOS = {
    "payments-api": payments_scenario,
    "shipping-api": shipping_scenario,
    "inventory-api": inventory_scenario,
    "auth-api": auth_scenario,
}


def bulk_insert(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """INSERT INTO probes (integration, ts, status_code, latency_ms, ok, error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def replay_breach_detection(conn: sqlite3.Connection, config: AppConfig, start: datetime, end: datetime) -> int:
    """Step through time in 30-minute increments and run the same
    metrics + breach-detection + cooldown logic the live prober uses, so
    the breaches table reflects realistic, cooldown-limited alert history
    instead of one row per probe."""
    checkpoint_step = timedelta(minutes=30)
    total_alerts = 0
    t = start
    while t <= end:
        for integration in config.integrations:
            metrics = service.get_metrics(conn, integration.name, config.window_hours, now=t)
            alerted = process_breaches(
                conn,
                integration_name=integration.name,
                metrics=metrics,
                sla=integration.sla,
                alert_config=config.alerts,
                cooldown_minutes=config.breach_cooldown_minutes,
                now=t,
            )
            total_alerts += len(alerted)
        t += checkpoint_step
    return total_alerts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5, help="days of synthetic history to generate")
    parser.add_argument("--config", type=str, default=os.environ.get("SLA_SENTINEL_CONFIG", "config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    if not config.integrations:
        print("No integrations configured; nothing to seed.", file=sys.stderr)
        sys.exit(1)

    db_path = config.db_path
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)

    print(f"Seeding {args.days} days of history ({iso(start)} -> {iso(end)}) into {db_path}\n")

    for integration in config.integrations:
        scenario = SCENARIOS.get(integration.name)
        if scenario is None:
            print(f"  (no scenario defined for {integration.name}, skipping)")
            continue
        rows = generate_probes(integration, start, end, scenario)
        bulk_insert(conn, rows)
        print(f"  {integration.name:<15} {len(rows):>5} probes inserted")

    print("\nReplaying breach detection across the trailing window (this uses the same")
    print("app.breaches / app.service code path the live prober uses)...")
    replay_start = end - timedelta(hours=36)
    alert_count = replay_breach_detection(conn, config, replay_start, end)
    print(f"  {alert_count} breach alert(s) recorded (after cooldown de-duplication)\n")

    print("Current SLA status (window = %.0fh, evaluated at %s):" % (config.window_hours, iso(end)))
    for integration in config.integrations:
        summary = service.integration_summary(conn, config, integration)
        m = summary["metrics"]
        print(
            f"  [{summary['status'].upper():<5}] {integration.name:<15} "
            f"uptime={m['uptime_pct']:>6}%  p95={m['p95_latency_ms']:>6}ms  "
            f"error_rate={m['error_rate_pct']:>5}%  probes={m['total_probes']}"
        )

    breach_rows = dbmod.fetch_breaches(conn, limit=10)
    print(f"\nMost recent breach records ({len(breach_rows)} shown, newest first):")
    for row in breach_rows:
        print(f"  {row['ts']}  {row['integration']:<15} {row['metric']:<15} {row['message']}")

    conn.close()
    print("\nDone. Start the app with:  uvicorn app.main:app --reload")
    print("Then open http://127.0.0.1:8000/")


if __name__ == "__main__":
    main()
