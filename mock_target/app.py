"""A tiny local FastAPI app that stands in for four real integrations, so
SLA Sentinel has something real to probe without needing network access or
paid services.

Run with:  uvicorn mock_target.app:app --port 9100

Endpoints (mirrors config.yaml):
  GET /payments/health   - fast and reliable
  GET /shipping/health   - occasionally slow (simulated latency degradation)
  GET /inventory/health  - occasionally errors/times out (simulated flakiness)
  GET /auth/health       - fast and reliable
"""

from __future__ import annotations

import asyncio
import random

from fastapi import FastAPI, Response

app = FastAPI(title="SLA Sentinel Mock Target")


@app.get("/payments/health")
async def payments_health():
    await asyncio.sleep(random.uniform(0.02, 0.08))
    return {"status": "ok", "service": "payments"}


@app.get("/shipping/health")
async def shipping_health():
    # Roughly 1 in 5 requests are noticeably slow, simulating a degrading
    # downstream dependency without ever fully failing.
    if random.random() < 0.2:
        await asyncio.sleep(random.uniform(0.6, 1.0))
    else:
        await asyncio.sleep(random.uniform(0.05, 0.15))
    return {"status": "ok", "service": "shipping"}


@app.get("/inventory/health")
async def inventory_health(response: Response):
    # Mostly healthy with occasional real failures, so a live demo can show
    # the prober recording genuine errors, not just historical seed data.
    roll = random.random()
    if roll < 0.03:
        response.status_code = 503
        return {"status": "error", "service": "inventory"}
    await asyncio.sleep(random.uniform(0.03, 0.1))
    return {"status": "ok", "service": "inventory"}


@app.get("/auth/health")
async def auth_health():
    await asyncio.sleep(random.uniform(0.01, 0.05))
    return {"status": "ok", "service": "auth"}
