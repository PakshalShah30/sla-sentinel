"""Cooldown logic tests: the same breach must not re-alert every cycle."""

from datetime import datetime, timedelta, timezone

from app import db as dbmod
from app.breaches import process_breaches, should_alert
from app.config import AlertConfig, SLAThresholds
from app.metrics import Metrics

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_should_alert_when_never_alerted_before():
    assert should_alert(None, NOW, cooldown_minutes=60) is True


def test_should_alert_false_within_cooldown_window():
    last = NOW - timedelta(minutes=10)
    assert should_alert(last, NOW, cooldown_minutes=60) is False


def test_should_alert_true_exactly_at_cooldown_boundary():
    last = NOW - timedelta(minutes=60)
    assert should_alert(last, NOW, cooldown_minutes=60) is True


def test_should_alert_true_after_cooldown_expires():
    last = NOW - timedelta(minutes=61)
    assert should_alert(last, NOW, cooldown_minutes=60) is True


def make_breaching_metrics():
    # Uptime (and, as its complement, error rate) both well outside any
    # reasonable SLA -> guaranteed breach on both metrics, latency within SLA.
    return Metrics(
        total_probes=100,
        ok_probes=50,
        uptime_pct=50.0,
        error_rate_pct=50.0,
        p50_latency_ms=100.0,
        p95_latency_ms=150.0,
        p99_latency_ms=180.0,
    )


def test_process_breaches_does_not_spam_within_cooldown(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)

    sla = SLAThresholds(uptime_pct=99.5, p95_latency_ms=400.0, error_rate_pct=1.0)
    alert_config = AlertConfig(mode="log")
    metrics = make_breaching_metrics()

    # First evaluation: breach is new, should alert and record.
    first = process_breaches(
        conn,
        integration_name="svc",
        metrics=metrics,
        sla=sla,
        alert_config=alert_config,
        cooldown_minutes=60,
        now=NOW,
    )
    assert {b.metric for b in first} == {"uptime_pct", "error_rate_pct"}
    assert len(dbmod.fetch_breaches(conn, limit=10)) == 2  # one row per breached metric

    # Second evaluation 10 minutes later: still breaching, but within the
    # cooldown window -> must NOT alert again or add new rows.
    second = process_breaches(
        conn,
        integration_name="svc",
        metrics=metrics,
        sla=sla,
        alert_config=alert_config,
        cooldown_minutes=60,
        now=NOW + timedelta(minutes=10),
    )
    assert second == []
    assert len(dbmod.fetch_breaches(conn, limit=10)) == 2  # unchanged

    # Third evaluation, after the cooldown has expired: should alert again.
    third = process_breaches(
        conn,
        integration_name="svc",
        metrics=metrics,
        sla=sla,
        alert_config=alert_config,
        cooldown_minutes=60,
        now=NOW + timedelta(minutes=61),
    )
    assert {b.metric for b in third} == {"uptime_pct", "error_rate_pct"}
    assert len(dbmod.fetch_breaches(conn, limit=10)) == 4

    conn.close()


def test_process_breaches_no_alert_when_within_sla(tmp_path):
    db_path = str(tmp_path / "test2.db")
    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)

    sla = SLAThresholds(uptime_pct=99.5, p95_latency_ms=400.0, error_rate_pct=1.0)
    alert_config = AlertConfig(mode="log")
    healthy_metrics = Metrics(
        total_probes=100,
        ok_probes=100,
        uptime_pct=100.0,
        error_rate_pct=0.0,
        p50_latency_ms=50.0,
        p95_latency_ms=90.0,
        p99_latency_ms=100.0,
    )

    result = process_breaches(
        conn,
        integration_name="svc",
        metrics=healthy_metrics,
        sla=sla,
        alert_config=alert_config,
        cooldown_minutes=60,
        now=NOW,
    )
    assert result == []
    assert dbmod.fetch_breaches(conn, limit=10) == []

    conn.close()
