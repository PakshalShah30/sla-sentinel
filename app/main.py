"""FastAPI application: JSON API + single-page dashboard.

Run with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from app import db as dbmod
from app import service
from app.config import load_config
from app.prober import Prober

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sla_sentinel.main")

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = BASE_DIR / "static" / "dashboard.html"

config = load_config()

# The prober runs on the event loop thread and owns this connection. API
# routes are sync and run in FastAPI's threadpool, so each request opens its
# own short-lived connection instead of sharing this one across threads.
conn = dbmod.connect(config.db_path)
dbmod.init_db(conn)

# Background probing can be disabled for pure demo/browsing sessions via env var.
ENABLE_PROBER = os.environ.get("SLA_SENTINEL_ENABLE_PROBER", "true").lower() not in ("0", "false", "no")
prober = Prober(config, conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if ENABLE_PROBER and config.integrations:
        prober.start()
        logger.info("Background prober enabled (set SLA_SENTINEL_ENABLE_PROBER=false to disable).")
    else:
        logger.info("Background prober disabled.")
    yield
    if ENABLE_PROBER:
        await prober.stop()


app = FastAPI(title="SLA Sentinel", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/integrations")
def api_integrations():
    with dbmod.get_conn(config.db_path) as req_conn:
        return [service.integration_summary(req_conn, config, integ) for integ in config.integrations]


@app.get("/api/metrics")
def api_metrics(window: str = Query(default="24h", description="e.g. 1h, 24h, 7d")):
    window_hours = service.parse_window(window, config.window_hours)
    result = []
    with dbmod.get_conn(config.db_path) as req_conn:
        for integ in config.integrations:
            summary = service.integration_summary(req_conn, config, integ, window_hours=window_hours)
            summary["latency_series"] = service.latency_series(req_conn, integ.name, window_hours)
            result.append(summary)
    return {"window": window, "window_hours": window_hours, "integrations": result}


@app.get("/api/breaches")
def api_breaches(limit: int = Query(default=50, ge=1, le=500)):
    with dbmod.get_conn(config.db_path) as req_conn:
        rows = dbmod.fetch_breaches(req_conn, limit=limit)
        return [dict(row) for row in rows]


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_PATH.read_text(encoding="utf-8")
