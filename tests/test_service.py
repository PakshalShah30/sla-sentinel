"""Tests for query-string window parsing and config path resolution."""

import os

import pytest

from app.config import load_config
from app.service import MAX_WINDOW_HOURS, parse_window

DEFAULT = 24.0


@pytest.mark.parametrize(
    "window, expected",
    [("24h", 24.0), ("7d", 168.0), ("90m", 1.5), ("12", 12.0), (" 2H ", 2.0)],
)
def test_parse_window_valid(window, expected):
    assert parse_window(window, DEFAULT) == expected


@pytest.mark.parametrize(
    "window",
    ["", "abc", "infh", "nanh", "-5h", "0h", "1e20h", f"{MAX_WINDOW_HOURS + 1}h"],
)
def test_parse_window_falls_back_to_default(window):
    assert parse_window(window, DEFAULT) == DEFAULT


def _write_config(path, db_path_line=""):
    path.write_text(
        f"{db_path_line}\nintegrations: []\n",
        encoding="utf-8",
    )


def test_relative_db_path_resolves_against_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("SLA_SENTINEL_DB_PATH", raising=False)
    monkeypatch.chdir("/")
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "db_path: data/x.db")
    assert load_config(str(cfg)).db_path == os.path.join(str(tmp_path), "data", "x.db")


def test_db_path_env_var_overrides_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SLA_SENTINEL_DB_PATH", "/tmp/override.db")
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "db_path: data/x.db")
    assert load_config(str(cfg)).db_path == "/tmp/override.db"
