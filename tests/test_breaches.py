"""Breach-detection tests, focused on threshold boundary behavior."""

from app.breaches import evaluate_breaches
from app.config import SLAThresholds
from app.metrics import Metrics

SLA = SLAThresholds(uptime_pct=99.5, p95_latency_ms=400.0, error_rate_pct=1.0)


def make_metrics(uptime_pct=100.0, p95=100.0, error_rate=0.0, total=1000):
    ok = int(total * uptime_pct / 100)
    return Metrics(
        total_probes=total,
        ok_probes=ok,
        uptime_pct=uptime_pct,
        error_rate_pct=error_rate,
        p50_latency_ms=p95 / 2,
        p95_latency_ms=p95,
        p99_latency_ms=p95 * 1.1,
    )


def test_uptime_exactly_at_threshold_is_not_a_breach():
    m = make_metrics(uptime_pct=99.5, error_rate=0.5)
    breaches = evaluate_breaches(m, SLA, "svc")
    assert [b.metric for b in breaches] == []


def test_uptime_just_below_threshold_is_a_breach():
    m = make_metrics(uptime_pct=99.49, error_rate=0.51)
    breaches = evaluate_breaches(m, SLA, "svc")
    metrics_breached = [b.metric for b in breaches]
    assert "uptime_pct" in metrics_breached
    uptime_breach = next(b for b in breaches if b.metric == "uptime_pct")
    assert round(uptime_breach.diff, 2) == 0.01


def test_p95_latency_exactly_at_threshold_is_not_a_breach():
    m = make_metrics(p95=400.0)
    breaches = evaluate_breaches(m, SLA, "svc")
    assert "p95_latency_ms" not in [b.metric for b in breaches]


def test_p95_latency_just_above_threshold_is_a_breach():
    m = make_metrics(p95=400.5)
    breaches = evaluate_breaches(m, SLA, "svc")
    latency_breach = next(b for b in breaches if b.metric == "p95_latency_ms")
    assert round(latency_breach.diff, 1) == 0.5


def test_error_rate_exactly_at_threshold_is_not_a_breach():
    m = make_metrics(uptime_pct=99.0, error_rate=1.0)
    breaches = evaluate_breaches(m, SLA, "svc")
    assert "error_rate_pct" not in [b.metric for b in breaches]


def test_error_rate_just_above_threshold_is_a_breach():
    m = make_metrics(uptime_pct=98.99, error_rate=1.01)
    breaches = evaluate_breaches(m, SLA, "svc")
    assert "error_rate_pct" in [b.metric for b in breaches]


def test_multiple_simultaneous_breaches_are_all_reported():
    m = make_metrics(uptime_pct=95.0, p95=900.0, error_rate=5.0)
    breaches = evaluate_breaches(m, SLA, "svc")
    assert {b.metric for b in breaches} == {"uptime_pct", "p95_latency_ms", "error_rate_pct"}


def test_no_latency_samples_skips_latency_check_without_crashing():
    m = Metrics(
        total_probes=5,
        ok_probes=0,
        uptime_pct=0.0,
        error_rate_pct=100.0,
        p50_latency_ms=None,
        p95_latency_ms=None,
        p99_latency_ms=None,
    )
    breaches = evaluate_breaches(m, SLA, "svc")
    assert "p95_latency_ms" not in [b.metric for b in breaches]
    assert "uptime_pct" in [b.metric for b in breaches]
