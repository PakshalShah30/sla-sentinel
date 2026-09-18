"""Metric math tests, with a hand-checked example for the percentile calc."""

from app.metrics import ProbeRecord, compute_metrics, percentile


def test_percentile_hand_checked():
    # Hand check: values 10..100 step 10 (n=10). Linear-interpolation p95:
    # rank = 0.95 * (10 - 1) = 8.55
    # floor=8 -> value 90, ceil=9 -> value 100, frac=0.55
    # result = 90 + 0.55 * (100 - 90) = 95.5
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert round(percentile(values, 95), 6) == 95.5

    # p50 of the same set: rank = 0.5 * 9 = 4.5 -> between values[4]=50 and
    # values[5]=60 -> 50 + 0.5*10 = 55.0
    assert round(percentile(values, 50), 6) == 55.0

    # p0 / p100 are just the min/max.
    assert percentile(values, 0) == 10
    assert percentile(values, 100) == 100


def test_percentile_single_value():
    assert percentile([42.0], 95) == 42.0


def test_uptime_and_error_rate_hand_checked():
    # 10 probes, 2 failures -> uptime 80%, error rate 20%. Hand-checked.
    records = [ProbeRecord(ok=True, latency_ms=100.0) for _ in range(8)]
    records += [ProbeRecord(ok=False, latency_ms=None) for _ in range(2)]
    m = compute_metrics(records)
    assert m.total_probes == 10
    assert m.ok_probes == 8
    assert m.uptime_pct == 80.0
    assert m.error_rate_pct == 20.0


def test_latency_percentiles_exclude_failed_probes_without_latency():
    records = [ProbeRecord(ok=True, latency_ms=float(v)) for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]]
    records.append(ProbeRecord(ok=False, latency_ms=None))  # timeout: no latency sample
    m = compute_metrics(records)
    assert m.total_probes == 11
    assert m.ok_probes == 10
    # uptime/error rate still count the failed probe...
    assert round(m.uptime_pct, 2) == round(10 / 11 * 100, 2)
    # ...but the latency percentiles are computed only over the 10 samples
    # that actually produced a latency, so the hand-checked p95 still holds.
    assert round(m.p95_latency_ms, 6) == 95.5
    assert round(m.p50_latency_ms, 6) == 55.0


def test_compute_metrics_empty_defaults_to_healthy_and_zero_probes():
    m = compute_metrics([])
    assert m.total_probes == 0
    assert m.uptime_pct == 100.0
    assert m.error_rate_pct == 0.0
    assert m.p95_latency_ms is None


def test_rounded_output_is_display_friendly():
    records = [ProbeRecord(ok=True, latency_ms=123.456) for _ in range(3)]
    m = compute_metrics(records)
    rounded = m.rounded()
    assert rounded["uptime_pct"] == 100.0
    assert rounded["p95_latency_ms"] == 123.0  # latency rounded to whole ms
    assert rounded["total_probes"] == 3
