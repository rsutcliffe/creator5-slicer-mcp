#!/usr/bin/env python3
"""Add this server to Claude Desktop's configuration.

Run it with the Python from the environment the server is installed in:

    .venv/bin/python scripts/install_claude_desktop.py

It keeps every existing setting, saves a backup beside the file first, and is
safe to run again. Quit and reopen Claude Desktop afterwards.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

NAME = "creator5-slicer"


def config_path() -> Path:
    override = os.environ.get("CLAUDE_DESKTOP_CONFIG")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def server_command() -> Path:
    folder = Path(sys.prefix) / ("Scripts" if platform.system() == "Windows" else "bin")
    for name in ("creator5-slicer-mcp", "creator5-slicer-mcp.exe"):
        if (folder / name).is_file():
            return folder / name
    raise SystemExit(
        f"The server is not installed in {sys.prefix}.\n"
        "From the project folder run:  .venv/bin/pip install -e .\n"
        "then run this script again with:  .venv/bin/python scripts/install_claude_desktop.py")


def main() -> int:
    command = server_command()
    path = config_path()
    config: dict = {}
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SystemExit(f"{path} is not valid JSON ({exc}). Nothing was changed.")
        if not isinstance(config, dict):
            raise SystemExit(f"{path} does not hold a JSON object. Nothing was changed.")
        backup = path.with_name(f"{path.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"Backup saved: {backup}")
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit("'mcpServers' in the config is not an object. Nothing was changed.")
    entry = servers.get(NAME) if isinstance(servers.get(NAME), dict) else {}
    entry["command"] = str(command)          # keeps any env block already there
    servers[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Added '{NAME}' to {path}")
    print(f"Command: {command}")
    print("Quit and reopen Claude Desktop to load it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
