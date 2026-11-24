"""Check an installation from the terminal, and remove it.

    creator5-slicer-mcp doctor
    creator5-slicer-mcp uninstall

`check_setup` answers the same questions from inside Claude, which is no help when the server will
not start or Claude is running an old copy. No command printed here needs a quotation mark.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, printcheck, profiles, slicer
from .config import ConfigError, load_settings

NAME = "creator5-slicer"
SETUP = "Run ./scripts/setup.sh in the folder this server was installed from."


def desktop_config() -> Path:
    """Claude Desktop's settings file. CLAUDE_DESKTOP_CONFIG overrides it, as it does for the installer."""
    override = os.environ.get("CLAUDE_DESKTOP_CONFIG")
    if override:
        return Path(override).expanduser()
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _entry(path: Path) -> dict | None:
    try:
        found = (json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {}).get(NAME)
        return found if isinstance(found, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def _here() -> Path:
    folder = Path(sys.prefix) / ("Scripts" if platform.system() == "Windows" else "bin")
    return folder / ("creator5-slicer-mcp.exe" if platform.system() == "Windows" else "creator5-slicer-mcp")


def checks(code_config: Path | None = None) -> list[tuple[bool, str, str]]:
    """Every check as (passed, what was found, how to fix it)."""
    out: list[tuple[bool, str, str]] = []
    settings = load_settings()
    if settings.slicer_path:
        release = slicer.app_version(settings)
        out.append((True, f"Slicer found: {settings.slicer_path}" + (f" (Flash Studio {release})" if release else ""), ""))
        if release and release != slicer.TESTED_FLASH_STUDIO:
            out.append((False, f"Flash Studio is {release}; this server was last tested with {slicer.TESTED_FLASH_STUDIO}.",
                        "An update can change defaults. Run the tests before trusting a slice: .venv/bin/python -m pytest -q"))
    else:
        out.append((False, "No slicer was found.", "Install Flash Studio, or set ORCA_SLICER_PATH to its executable."))
    try:
        folder = settings.require_profiles()
        presets = profiles.process_presets(folder, "0.4")
        out.append((True, f"Creator 5 presets readable: {len(presets)} process presets for the 0.4 mm nozzle.", ""))
    except (ConfigError, profiles.ProfileError) as exc:
        out.append((False, "The Creator 5 presets cannot be read.", str(exc)))
    try:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.output_dir / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        out.append((True, f"Prints folder writable: {settings.output_dir}", ""))
    except OSError as exc:
        out.append((False, f"The prints folder cannot be written: {settings.output_dir}", f"{exc}. Set C5_OUTPUT_DIR to a folder you own."))
    cad = printcheck.version()
    out.append((bool(cad), f"Print check tool: {cad}" if cad else "The print check tool (cad) is not installed; shapes are sliced unchecked.",
                "" if cad else "Run: uv tool install cad"))
    for module, what, fix in (("PIL", "Pictures (Pillow)", SETUP), ("cascadio", "STEP files (cascadio)", SETUP)):
        found = importlib.util.find_spec(module) is not None
        out.append((found, f"{what}: installed" if found else f"{what}: not installed.", "" if found else fix))
    here = _here()
    out.append((here.is_file(), f"This copy: version {__version__} at {here}" if here.is_file() else f"The server command is missing: {here}",
                "" if here.is_file() else SETUP))
    seen = False
    for app, path in (("Claude Desktop", desktop_config()), ("Claude Code", code_config or Path.home() / ".claude.json")):
        entry = _entry(path)
        if not entry:
            out.append((True, f"{app}: not connected to this server.", ""))
            continue
        seen = True
        same = Path(str(entry.get("command", ""))).resolve() == here.resolve()
        out.append((same, f"{app}: connected to this copy." if same else f"{app} runs another copy: {entry.get('command')}",
                    "" if same else SETUP + " It points Claude at this copy."))
    if not seen:
        out.append((False, "Neither Claude Desktop nor Claude Code is connected to this server.", SETUP))
    return out


def doctor() -> int:
    results = checks()
    for passed, found, fix in results:
        print(("OK   " if passed else "FIX  ") + found)
        if not passed and fix:
            print("     " + fix)
    failed = sum(1 for passed, _, _ in results if not passed)
    print()
    print("Everything checks out. After an update, quit and reopen Claude so it loads the new code." if not failed
          else f"{failed} thing{'s' if failed > 1 else ''} to fix, marked FIX above.")
    return 1 if failed else 0


def uninstall() -> int:
    """Disconnect this server from Claude Desktop and Claude Code. Projects and the spool register are kept."""
    path = desktop_config()
    if _entry(path):
        config = json.loads(path.read_text(encoding="utf-8"))
        backup = path.with_name(f"{path.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        del config["mcpServers"][NAME]
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Removed from Claude Desktop ({path}). Backup saved: {backup}")
    else:
        print("Claude Desktop: not connected, nothing to remove.")
    claude = shutil.which("claude")
    if claude:
        done = subprocess.run([claude, "mcp", "remove", NAME, "--scope", "user"], capture_output=True, text=True, timeout=60)
        print("Removed from Claude Code." if done.returncode == 0 else "Claude Code: not connected, nothing to remove.")
    else:
        print("Claude Code: the claude command was not found, so nothing was changed there.")
    print(f"Your projects, print log and spools in {load_settings().output_dir} were left alone.")
    print("Quit and reopen Claude to finish.")
    return 0


def main(argv: list[str]) -> int:
    return doctor() if argv[0] == "doctor" else uninstall()
