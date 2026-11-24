#!/usr/bin/env python3
"""Build the Claude Desktop bundle: dist/creator5-slicer-mcp-<version>.mcpb.

    .venv/bin/python scripts/build_bundle.py

A bundle is a zip holding manifest.json and the server (the MCPB manifest spec, version 0.4, at
github.com/modelcontextprotocol/mcpb). It uses the "uv" server type: Claude Desktop installs the
dependencies named in pyproject.toml itself, so no compiled library is packed in. Opening the file
in Claude Desktop installs the server.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def manifest(version: str) -> dict:
    return {
        "manifest_version": "0.4",
        "name": "creator5-slicer-mcp",
        "display_name": "Creator 5 Slicer",
        "version": version,
        "description": "Slices models for the Flashforge Creator 5 with Flash Studio, choosing and checking the settings.",
        "long_description": "Name a model, a filament and what the part is for. The server picks presets and settings, checks the "
                            "shape and the sliced result, draws the plate, and opens the project in Flash Studio ready to print. "
                            "Flash Studio must be installed. Nothing is sent to the printer.",
        "author": {"name": "rsutcliffe", "url": "https://github.com/rsutcliffe"},
        "repository": {"type": "git", "url": "https://github.com/rsutcliffe/creator5-slicer-mcp"},
        "homepage": "https://github.com/rsutcliffe/creator5-slicer-mcp",
        "support": "https://github.com/rsutcliffe/creator5-slicer-mcp/issues",
        "license": "MIT",
        "keywords": ["3d printing", "slicer", "flashforge", "creator 5", "flash studio"],
        "server": {
            "type": "uv",
            "entry_point": "src/creator5_mcp/server.py",
            # The console script, with the STEP reader's extra, run from the bundle's own folder.
            "mcp_config": {"command": "uv", "args": ["run", "--directory", "${__dirname}", "--extra", "step", "creator5-slicer-mcp"]},
        },
        "tools_generated": True,
        "compatibility": {"platforms": ["darwin", "win32", "linux"], "runtimes": {"python": ">=3.10"}},
    }


def build(out_dir: Path) -> Path:
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"creator5-slicer-mcp-{version}.mcpb"
    files = [ROOT / name for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")]
    files += sorted((ROOT / "src" / "creator5_mcp").glob("*.py"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest(version), indent=2) + "\n")
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    return out


if __name__ == "__main__":
    made = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
    print(f"Built {made} ({made.stat().st_size // 1024} KB). Open it in Claude Desktop to install.")
