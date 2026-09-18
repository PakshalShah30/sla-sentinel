"""Rolling-window SLA metric computation.

Pure functions only — no I/O — so the math can be hand-checked and unit
tested in isolation from the database and the scheduler.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def window_start(window_hours: float, now: Optional[datetime] = None) -> datetime:
    now = now or utcnow()
    return now - timedelta(hours=window_hours)


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (the same convention as numpy's
    default 'linear' method). `pct` is 0-100.

    Example (hand-checked): values = [10,20,...,100], pct=95 ->
    index = 0.95 * 9 = 8.55 -> interpolate between values[8]=90 and
    values[9]=100 -> 90 + 0.55*(100-90) = 95.5
    """
    if not values:
        raise ValueError("percentile() requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    frac = rank - lo
    return ordered[lo] + frac * (ordered[hi] - ordered[lo])


@dataclass
class ProbeRecord:
    ok: bool
    latency_ms: Optional[float]


@dataclass
class Metrics:
    total_probes: int
    ok_probes: int
    uptime_pct: float
    error_rate_pct: float
    p50_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    p99_latency_ms: Optional[float]

    def rounded(self) -> dict:
        def r(x, ndigits=1):
            return None if x is None else round(x, ndigits)

        return {
            "total_probes": self.total_probes,
            "ok_probes": self.ok_probes,
            "uptime_pct": r(self.uptime_pct, 2),
            "error_rate_pct": r(self.error_rate_pct, 2),
            "p50_latency_ms": r(self.p50_latency_ms, 0),
            "p95_latency_ms": r(self.p95_latency_ms, 0),
            "p99_latency_ms": r(self.p99_latency_ms, 0),
        }


def compute_metrics(probes: Iterable[ProbeRecord]) -> Metrics:
    """Compute uptime / latency percentiles / error rate over a set of probes.

    - uptime_pct is the share of probes that succeeded (matched expected
      status and returned within the timeout).
    - error_rate_pct is simply the complement of uptime_pct: every probe is
      either a success or a failure in this model, so they always sum to
      100. Kept as an explicit, separately-named metric because SLA
      contracts usually quote it that way and because it reads more
      naturally next to a max threshold ("error rate must stay <= 1%")
      than "uptime must stay >= 99%" does for the same fact.
    - latency percentiles are computed only over probes that produced a
      latency measurement (i.e. got any HTTP response back). A probe that
      timed out or hit a connection error has no latency sample and is
      excluded from the percentile calculation, but still counts against
      uptime.
    """
    probes = list(probes)
    total = len(probes)
    if total == 0:
        return Metrics(
            total_probes=0,
            ok_probes=0,
            uptime_pct=100.0,
            error_rate_pct=0.0,
            p50_latency_ms=None,
            p95_latency_ms=None,
            p99_latency_ms=None,
        )

    ok_count = sum(1 for p in probes if p.ok)
    uptime_pct = (ok_count / total) * 100.0
    error_rate_pct = 100.0 - uptime_pct

    latencies = [p.latency_ms for p in probes if p.latency_ms is not None]
    if latencies:
        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        p99 = percentile(latencies, 99)
    else:
        p50 = p95 = p99 = None

    return Metrics(
        total_probes=total,
        ok_probes=ok_count,
        uptime_pct=uptime_pct,
        error_rate_pct=error_rate_pct,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        p99_latency_ms=p99,
    )
