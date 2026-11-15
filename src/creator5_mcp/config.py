"""Locate the slicer, its bundled profiles and the output folder.

Everything is overridable with environment variables so the server works with
stock OrcaSlicer or with Flashforge's own fork (Flash Studio / Orca-Flashforge),
which shares the same command line.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

# (binary, profiles dir) pairs tried in order when no override is set.
_MAC_APPS = [
    "/Applications/OrcaSlicer.app",
    "/Applications/Flash Studio.app",
    "/Applications/Orca-Flashforge.app",
]
_WIN_DIRS = [
    r"C:\Program Files\OrcaSlicer",
    r"C:\Program Files\Flash Studio",
    r"C:\Program Files\Orca-Flashforge",
]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    slicer_path: Path | None
    profiles_dir: Path | None
    output_dir: Path
    printer_ip: str | None
    printer_serial: str | None
    printer_check_code: str | None

    def require_slicer(self) -> Path:
        if self.slicer_path is None:
            raise ConfigError(
                "OrcaSlicer was not found. Install it, or set ORCA_SLICER_PATH to the "
                "executable (on macOS: /Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer)."
            )
        return self.slicer_path

    def require_profiles(self) -> Path:
        if self.profiles_dir is None:
            raise ConfigError(
                "The slicer's profiles folder was not found. Set ORCA_PROFILES_DIR to the "
                "folder holding Flashforge/ and OrcaFilamentLibrary/ "
                "(on macOS: /Applications/OrcaSlicer.app/Contents/Resources/profiles)."
            )
        return self.profiles_dir


def _mac_binary(app: Path) -> Path | None:
    macos = app / "Contents" / "MacOS"
    if not macos.is_dir():
        return None
    for name in ("OrcaSlicer", "Flash Studio", "Orca-Flashforge", app.stem):
        if (macos / name).is_file():
            return macos / name
    files = [p for p in macos.iterdir() if p.is_file()]
    return files[0] if files else None


def _candidates() -> list[tuple[Path, Path]]:
    system = platform.system()
    out: list[tuple[Path, Path]] = []
    if system == "Darwin":
        for app in _MAC_APPS + [str(Path.home() / a.lstrip("/")) for a in _MAC_APPS]:
            binary = _mac_binary(Path(app))
            if binary:
                out.append((binary, Path(app) / "Contents" / "Resources" / "profiles"))
    elif system == "Windows":
        for d in _WIN_DIRS:
            for exe in ("orca-slicer.exe", "OrcaSlicer.exe", "orca-flashforge.exe"):
                if (Path(d) / exe).is_file():
                    out.append((Path(d) / exe, Path(d) / "resources" / "profiles"))
    else:
        for name in ("orca-slicer", "OrcaSlicer"):
            found = shutil.which(name)
            if found:
                binary = Path(found).resolve()
                for res in (binary.parent / "resources", binary.parent.parent / "share" / "OrcaSlicer",
                            binary.parent.parent / "resources"):
                    out.append((binary, res / "profiles"))
    return out


def load_settings() -> Settings:
    env = os.environ
    slicer = Path(env["ORCA_SLICER_PATH"]).expanduser() if env.get("ORCA_SLICER_PATH") else None
    profiles = Path(env["ORCA_PROFILES_DIR"]).expanduser() if env.get("ORCA_PROFILES_DIR") else None

    for binary, prof in _candidates():
        if slicer is None:
            slicer = binary
        if profiles is None and (prof / "Flashforge").is_dir():
            profiles = prof
    if slicer is not None and not slicer.is_file():
        slicer = None
    if profiles is not None and not (profiles / "Flashforge").is_dir():
        profiles = None

    output = Path(env.get("C5_OUTPUT_DIR", str(Path.home() / "Creator5Prints"))).expanduser()
    return Settings(
        slicer_path=slicer,
        profiles_dir=profiles,
        output_dir=output,
        printer_ip=env.get("C5_PRINTER_IP") or None,
        printer_serial=env.get("C5_PRINTER_SERIAL") or None,
        printer_check_code=env.get("C5_PRINTER_CHECK_CODE") or None,
    )
