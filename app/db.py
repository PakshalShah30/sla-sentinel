"""SQLite storage layer for SLA Sentinel.

Kept deliberately simple: three tables (probes, breaches, breach_state) and
a handful of plain functions. No ORM — at this scale raw SQL is easier to
reason about and to hand-check in tests.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    integration TEXT NOT NULL,
    ts TEXT NOT NULL,               -- ISO-8601 UTC timestamp
    status_code INTEGER,            -- NULL on connection error / timeout
    latency_ms REAL,                -- NULL when no response was received
    ok INTEGER NOT NULL,            -- 1 = success (matched expected status), 0 = failure
    error TEXT                      -- short error description, if any
);
CREATE INDEX IF NOT EXISTS idx_probes_integration_ts ON probes (integration, ts);

CREATE TABLE IF NOT EXISTS breaches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    integration TEXT NOT NULL,
    ts TEXT NOT NULL,                -- when the breach was detected/recorded
    metric TEXT NOT NULL,            -- 'uptime_pct' | 'p95_latency_ms' | 'error_rate_pct'
    threshold REAL NOT NULL,
    actual REAL NOT NULL,
    diff REAL NOT NULL,              -- signed magnitude by which the SLA was missed
    message TEXT NOT NULL,
    alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_breaches_integration_ts ON breaches (integration, ts);

CREATE TABLE IF NOT EXISTS breach_state (
    integration TEXT NOT NULL,
    metric TEXT NOT NULL,
    last_alert_ts TEXT NOT NULL,
    PRIMARY KEY (integration, metric)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def get_conn(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()


def insert_probe(
    conn: sqlite3.Connection,
    *,
    integration: str,
    ts: str,
    status_code: Optional[int],
    latency_ms: Optional[float],
    ok: bool,
    error: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO probes (integration, ts, status_code, latency_ms, ok, error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (integration, ts, status_code, latency_ms, 1 if ok else 0, error),
    )
    conn.commit()


def fetch_probes(
    conn: sqlite3.Connection,
    integration: str,
    since_ts: str,
    until_ts: Optional[str] = None,
) -> list[sqlite3.Row]:
    if until_ts is None:
        cur = conn.execute(
            """SELECT * FROM probes WHERE integration = ? AND ts >= ? ORDER BY ts ASC""",
            (integration, since_ts),
        )
    else:
        cur = conn.execute(
            """SELECT * FROM probes WHERE integration = ? AND ts >= ? AND ts <= ?
               ORDER BY ts ASC""",
            (integration, since_ts, until_ts),
        )
    return cur.fetchall()


def insert_breach(
    conn: sqlite3.Connection,
    *,
    integration: str,
    ts: str,
    metric: str,
    threshold: float,
    actual: float,
    diff: float,
    message: str,
    alerted: bool,
) -> int:
    cur = conn.execute(
        """INSERT INTO breaches (integration, ts, metric, threshold, actual, diff, message, alerted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (integration, ts, metric, threshold, actual, diff, message, 1 if alerted else 0),
    )
    conn.commit()
    return cur.lastrowid


def fetch_breaches(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    cur = conn.execute(
        """SELECT * FROM breaches ORDER BY ts DESC LIMIT ?""",
        (limit,),
    )
    return cur.fetchall()


def get_last_alert_ts(conn: sqlite3.Connection, integration: str, metric: str) -> Optional[str]:
    cur = conn.execute(
        """SELECT last_alert_ts FROM breach_state WHERE integration = ? AND metric = ?""",
        (integration, metric),
    )
    row = cur.fetchone()
    return row["last_alert_ts"] if row else None


def set_last_alert_ts(conn: sqlite3.Connection, integration: str, metric: str, ts: str) -> None:
    conn.execute(
        """INSERT INTO breach_state (integration, metric, last_alert_ts)
           VALUES (?, ?, ?)
           ON CONFLICT(integration, metric) DO UPDATE SET last_alert_ts = excluded.last_alert_ts""",
        (integration, metric, ts),
    )
    conn.commit()
