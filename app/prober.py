"""Async probe engine.

Each integration gets its own asyncio task that loops forever on its own
`interval_seconds`, hits the endpoint, and writes one row to the `probes`
table per attempt. Timeouts and connection errors are recorded as failed
probes (ok=0) — they are expected, routine outcomes for a monitor to
observe, not bugs, so they must never crash the loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app import db as dbmod
from app.config import AppConfig, Integration
from app.metrics import iso, utcnow

logger = logging.getLogger("sla_sentinel.prober")


@dataclass
class ProbeOutcome:
    status_code: Optional[int]
    latency_ms: Optional[float]
    ok: bool
    error: Optional[str]


async def probe_once(client: httpx.AsyncClient, integration: Integration) -> ProbeOutcome:
    """Issue a single probe request. Never raises."""
    start = time.perf_counter()
    try:
        response = await client.request(
            integration.method,
            integration.url,
            timeout=integration.timeout_seconds,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        ok = response.status_code == integration.expected_status
        error = None if ok else f"unexpected status {response.status_code}"
        return ProbeOutcome(status_code=response.status_code, latency_ms=latency_ms, ok=ok, error=error)
    except httpx.TimeoutException as exc:
        return ProbeOutcome(status_code=None, latency_ms=None, ok=False, error=f"timeout: {exc}")
    except httpx.ConnectError as exc:
        return ProbeOutcome(status_code=None, latency_ms=None, ok=False, error=f"connection error: {exc}")
    except httpx.HTTPError as exc:
        return ProbeOutcome(status_code=None, latency_ms=None, ok=False, error=f"http error: {exc}")
    except Exception as exc:  # noqa: BLE001 - a probe must never crash its loop
        return ProbeOutcome(status_code=None, latency_ms=None, ok=False, error=f"unexpected error: {exc}")


def record_outcome(conn: sqlite3.Connection, integration: Integration, outcome: ProbeOutcome) -> None:
    dbmod.insert_probe(
        conn,
        integration=integration.name,
        ts=iso(utcnow()),
        status_code=outcome.status_code,
        latency_ms=outcome.latency_ms,
        ok=outcome.ok,
        error=outcome.error,
    )


class Prober:
    """Owns one background asyncio task per enabled integration."""

    def __init__(self, config: AppConfig, conn: sqlite3.Connection):
        self.config = config
        self.conn = conn
        self._tasks: list[asyncio.Task] = []
        self._stopping = False

    async def _loop_one(self, integration: Integration) -> None:
        async with httpx.AsyncClient() as client:
            while not self._stopping:
                outcome = await probe_once(client, integration)
                try:
                    record_outcome(self.conn, integration, outcome)
                    self._on_probe_recorded(integration)
                except Exception:  # noqa: BLE001 - storage hiccups must not kill the loop
                    logger.exception("Failed to record/evaluate probe for %s", integration.name)
                await asyncio.sleep(integration.interval_seconds)

    def _on_probe_recorded(self, integration: Integration) -> None:
        # Imported lazily to avoid a circular import (service -> prober not needed,
        # but keeps this module import-order agnostic).
        from app.service import evaluate_integration

        evaluate_integration(self.conn, self.config, integration)

    def start(self) -> None:
        self._stopping = False
        for integration in self.config.integrations:
            if integration.enabled:
                self._tasks.append(asyncio.create_task(self._loop_one(integration)))
        logger.info("Prober started for %d integration(s)", len(self._tasks))

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
