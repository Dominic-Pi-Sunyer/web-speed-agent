#!/usr/bin/env python3
"""Compatibility shim — the server now lives in web_speed_agent/mcp_server.py.

It moved so it actually ships: hatchling only packages the `web_speed_agent`
package, so a root-level module was silently excluded from every wheel. Anyone
who ran `pip install web-speed-agent` got the SDK and no MCP server.

This file stays because existing MCP host configs point at it by absolute path
(`"args": [".../agent_mcp_server.py"]`). Deleting it would break every install
already in the wild. New installs should use the console script instead:

    "command": "webspeed-agent"

which needs no path at all and works identically on Windows, macOS and Linux.
"""
from __future__ import annotations

import os
import sys

# Importable whether this file is run from a checkout or a copied location.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web_speed_agent.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
