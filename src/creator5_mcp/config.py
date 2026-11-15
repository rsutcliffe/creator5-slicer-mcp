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
                "No readable Creator 5 presets were found. OrcaSlicer 2.5 packs its presets into "
                ".opc files this server cannot read. Install Flash Studio (its presets are plain "
                "JSON and are picked up automatically), or set ORCA_PROFILES_DIR to a folder that "
                "holds Flashforge/machine, Flashforge/process and Flashforge/filament as JSON files."
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


def has_loose_presets(profiles: Path) -> bool:
    """True when the folder holds Flashforge presets as plain JSON files.

    OrcaSlicer 2.5 ships its presets packed into `<vendor>.opc` files, which
    this server cannot read; Flash Studio and older OrcaSlicer builds ship
    plain JSON.
    """
    return (profiles / "Flashforge" / "machine").is_dir()


def _user_data_dirs(app_name: str) -> list[Path]:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path.home() / ".config"
    return [base / app_name / "system"]


def _candidates() -> list[tuple[Path, list[Path]]]:
    """Installed slicers, each with the places its presets may live, best first."""
    system = platform.system()
    out: list[tuple[Path, list[Path]]] = []
    if system == "Darwin":
        for app in _MAC_APPS + [str(Path.home() / a.lstrip("/")) for a in _MAC_APPS]:
            binary = _mac_binary(Path(app))
            if binary:
                out.append((binary, [Path(app) / "Contents" / "Resources" / "profiles",
                                     *_user_data_dirs(Path(app).stem)]))
    elif system == "Windows":
        for d in _WIN_DIRS:
            for exe in ("orca-slicer.exe", "OrcaSlicer.exe", "orca-flashforge.exe", "Flash Studio.exe"):
                if (Path(d) / exe).is_file():
                    out.append((Path(d) / exe, [Path(d) / "resources" / "profiles", *_user_data_dirs(Path(d).name)]))
    else:
        for name in ("orca-slicer", "OrcaSlicer"):
            found = shutil.which(name)
            if found:
                binary = Path(found).resolve()
                out.append((binary, [binary.parent / "resources" / "profiles",
                                     binary.parent.parent / "share" / "OrcaSlicer" / "profiles",
                                     binary.parent.parent / "resources" / "profiles",
                                     *_user_data_dirs("OrcaSlicer")]))
    return out


def _pick(explicit_slicer: Path | None, explicit_profiles: Path | None) -> tuple[Path | None, Path | None]:
    """Choose a slicer and a presets folder that belong together.

    A slicer whose presets are readable beats one whose presets are packed,
    so that settings and the program reading them always match.
    """
    if explicit_slicer is not None and not explicit_slicer.is_file():
        explicit_slicer = None
    if explicit_profiles is not None and not has_loose_presets(explicit_profiles):
        explicit_profiles = None
    if explicit_slicer and explicit_profiles:
        return explicit_slicer, explicit_profiles

    candidates = _candidates()
    if explicit_slicer:
        candidates = [c for c in candidates if c[0] == explicit_slicer] or [(explicit_slicer, [])]
    first_slicer = explicit_slicer
    for binary, folders in candidates:
        first_slicer = first_slicer or binary
        if explicit_profiles:
            return binary, explicit_profiles
        for folder in folders:
            if has_loose_presets(folder):
                return binary, folder
    return first_slicer, explicit_profiles


def load_settings() -> Settings:
    env = os.environ
    slicer, profiles = _pick(
        Path(env["ORCA_SLICER_PATH"]).expanduser() if env.get("ORCA_SLICER_PATH") else None,
        Path(env["ORCA_PROFILES_DIR"]).expanduser() if env.get("ORCA_PROFILES_DIR") else None)

    output = Path(env.get("C5_OUTPUT_DIR", str(Path.home() / "Creator5Prints"))).expanduser()
    return Settings(
        slicer_path=slicer,
        profiles_dir=profiles,
        output_dir=output,
        printer_ip=env.get("C5_PRINTER_IP") or None,
        printer_serial=env.get("C5_PRINTER_SERIAL") or None,
        printer_check_code=env.get("C5_PRINTER_CHECK_CODE") or None,
    )
