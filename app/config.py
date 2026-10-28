"""Configuration loading for SLA Sentinel.

Reads config.yaml (or a path given via the SLA_SENTINEL_CONFIG env var) and
exposes typed, validated objects the rest of the app can use. No secrets
live in the YAML file — the Slack webhook URL, if used, is read from an
environment variable named in the config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

# Default to the repo-root config.yaml so the app and scripts work no matter
# which directory they're launched from. SLA_SENTINEL_CONFIG overrides it.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.environ.get("SLA_SENTINEL_CONFIG", os.path.join(BASE_DIR, "config.yaml"))


@dataclass
class SLAThresholds:
    uptime_pct: float
    p95_latency_ms: float
    error_rate_pct: float


@dataclass
class Integration:
    name: str
    url: str
    method: str
    expected_status: int
    timeout_seconds: float
    interval_seconds: float
    sla: SLAThresholds
    enabled: bool = True


@dataclass
class AlertConfig:
    mode: str = "log"  # "log" or "slack"
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"

    @property
    def slack_webhook_url(self) -> Optional[str]:
        return os.environ.get(self.slack_webhook_env)


@dataclass
class AppConfig:
    window_hours: float = 24.0
    breach_cooldown_minutes: float = 60.0
    alerts: AlertConfig = field(default_factory=AlertConfig)
    integrations: list[Integration] = field(default_factory=list)
    db_path: str = "data/sla_sentinel.db"


def load_config(path: str = None) -> AppConfig:
    """Load and validate the YAML config into an AppConfig object."""
    path = path or DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    alerts_raw = raw.get("alerts", {}) or {}
    alerts = AlertConfig(
        mode=alerts_raw.get("mode", "log"),
        slack_webhook_env=alerts_raw.get("slack_webhook_env", "SLACK_WEBHOOK_URL"),
    )

    integrations = []
    for item in raw.get("integrations", []) or []:
        sla_raw = item.get("sla", {}) or {}
        sla = SLAThresholds(
            uptime_pct=float(sla_raw["uptime_pct"]),
            p95_latency_ms=float(sla_raw["p95_latency_ms"]),
            error_rate_pct=float(sla_raw["error_rate_pct"]),
        )
        integrations.append(
            Integration(
                name=item["name"],
                url=item["url"],
                method=item.get("method", "GET").upper(),
                expected_status=int(item.get("expected_status", 200)),
                timeout_seconds=float(item.get("timeout_seconds", 5)),
                interval_seconds=float(item.get("interval_seconds", 60)),
                sla=sla,
                enabled=bool(item.get("enabled", True)),
            )
        )

    db_path = os.environ.get("SLA_SENTINEL_DB_PATH")
    if not db_path:
        # A relative db_path in the YAML is relative to the config file, not cwd.
        db_path = raw.get("db_path", "data/sla_sentinel.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(os.path.abspath(path)), db_path)

    return AppConfig(
        window_hours=float(raw.get("window_hours", 24)),
        breach_cooldown_minutes=float(raw.get("breach_cooldown_minutes", 60)),
        alerts=alerts,
        integrations=integrations,
        db_path=db_path,
    )
