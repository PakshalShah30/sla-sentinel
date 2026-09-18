"""Alert dispatch: log-only by default, optional Slack webhook.

The webhook URL is never read from config.yaml directly — only the name of
an environment variable is configured, and the URL itself is read from the
environment at dispatch time. This keeps secrets out of git entirely.
"""

from __future__ import annotations

import logging

import httpx

from app.config import AlertConfig

logger = logging.getLogger("sla_sentinel.alerts")


def dispatch_alert(alert_config: AlertConfig, *, title: str, message: str) -> None:
    """Send a breach alert according to the configured mode.

    Never raises: an alert-delivery failure should not crash the prober or
    the breach-detection loop. Errors are logged instead.
    """
    logger.warning("SLA BREACH: %s | %s", title, message)

    if alert_config.mode != "slack":
        return

    webhook_url = alert_config.slack_webhook_url
    if not webhook_url:
        logger.info(
            "Slack alert mode is enabled but %s is not set; falling back to log-only.",
            alert_config.slack_webhook_env,
        )
        return

    try:
        payload = {"text": f"*{title}*\n{message}"}
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(webhook_url, json=payload)
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - alerting must never crash the caller
        logger.error("Failed to deliver Slack alert: %s", exc)
