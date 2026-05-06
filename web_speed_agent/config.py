"""Config file + environment variable handling."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_DEFAULT_SERVER_URL = "https://api.getwebspeed.io"
_CONFIG_FILE = "config.yaml"


class Config:
    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        self._file = config_dir / _CONFIG_FILE
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "sessions").mkdir(exist_ok=True)
        (self._dir / "logs").mkdir(exist_ok=True)

        if self._file.exists():
            with open(self._file) as f:
                self._data = yaml.safe_load(f) or {}

    def _save(self) -> None:
        with open(self._file, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False)

    @property
    def api_key(self) -> str | None:
        # Env var takes precedence
        return (
            os.getenv("WEBSPEED_API_KEY")
            or self._data.get("api", {}).get("key")
        )

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._data.setdefault("api", {})["key"] = value
        self._save()

    @property
    def server_url(self) -> str:
        return (
            os.getenv("WEBSPEED_SERVER_URL")
            or self._data.get("api", {}).get("server_url")
            or _DEFAULT_SERVER_URL
        )

    @property
    def headless(self) -> bool:
        return self._data.get("browser", {}).get("headless", True)

    @property
    def timeout(self) -> int:
        return int(self._data.get("api", {}).get("timeout", 30))

    @property
    def sessions_dir(self) -> Path:
        return self._dir / "sessions"
