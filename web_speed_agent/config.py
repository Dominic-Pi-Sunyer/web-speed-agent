"""Config file + environment variable handling."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import yaml

_DEFAULT_SERVER_URL = "https://api.getwebspeed.io"
_CONFIG_FILE = "config.yaml"


def _secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _secure_write(path: Path, content: str) -> None:
    """Write file with owner-only permissions (0o600)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


class Config:
    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        self._file = config_dir / _CONFIG_FILE
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        _secure_mkdir(self._dir)
        _secure_mkdir(self._dir / "sessions")
        _secure_mkdir(self._dir / "logs")

        if self._file.exists():
            # Warn if file is world-readable
            mode = self._file.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                import warnings
                warnings.warn(
                    f"Config file {self._file} is readable by others. "
                    "Run: chmod 600 ~/.webspeed/config.yaml",
                    UserWarning,
                    stacklevel=3,
                )
            with open(self._file) as f:
                self._data = yaml.safe_load(f) or {}

    def _save(self) -> None:
        _secure_write(self._file, yaml.dump(self._data, default_flow_style=False))

    @property
    def api_key(self) -> str | None:
        # Env var always takes precedence — preferred way to provide key
        return (
            os.getenv("WEBSPEED_API_KEY")
            or self._data.get("api", {}).get("key")
        )

    @property
    def server_url(self) -> str:
        url = (
            os.getenv("WEBSPEED_SERVER_URL")
            or self._data.get("api", {}).get("server_url")
            or _DEFAULT_SERVER_URL
        )
        if not url.startswith("https://"):
            raise ValueError(
                f"server_url must use HTTPS (got {url!r}). "
                "API keys must not be transmitted over plain HTTP."
            )
        return url

    @property
    def headless(self) -> bool:
        return self._data.get("browser", {}).get("headless", True)

    @property
    def timeout(self) -> int:
        return int(self._data.get("api", {}).get("timeout", 30))

    @property
    def sessions_dir(self) -> Path:
        return self._dir / "sessions"
