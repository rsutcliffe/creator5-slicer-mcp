"""Read the slicer's bundled presets and flatten their inheritance chains.

OrcaSlicer presets are JSON files that inherit from one another, sometimes
across vendor folders (Flashforge filaments inherit from OrcaFilamentLibrary).
Not every slicer build resolves that chain on the command line, so this module
resolves it here and writes self-contained preset files.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

KINDS = ("machine", "process", "filament")
VENDORS = ("Flashforge", "OrcaFilamentLibrary")
NOZZLES = ("0.25", "0.4", "0.6", "0.8")


class ProfileError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _index(profiles_dir: str) -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    for vendor in VENDORS:
        for kind in KINDS:
            folder = Path(profiles_dir) / vendor / kind
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                name = data.get("name") if isinstance(data, dict) else None
                # Flashforge wins over the shared library on a name clash.
                if name and (kind, name) not in index:
                    index[(kind, name)] = data
    if not index:
        raise ProfileError(f"No presets found under {profiles_dir}")
    return index


def flatten(profiles_dir: Path, kind: str, name: str) -> dict:
    """Return preset `name` with every inherited value merged in."""
    index = _index(str(profiles_dir))
    chain: list[dict] = []
    seen: set[str] = set()
    current: str | None = name
    while current:
        if current in seen:
            raise ProfileError(f"Circular inheritance at {kind} preset '{current}'")
        seen.add(current)
        data = index.get((kind, current))
        if data is None:
            raise ProfileError(f"{kind} preset '{current}' not found (needed by '{name}')")
        chain.append(data)
        current = data.get("inherits") or None
    merged: dict = {}
    for data in reversed(chain):
        merged.update(data)
    merged.pop("inherits", None)
    merged["name"] = name
    merged["type"] = kind
    merged["from"] = "system"
    return merged


def machine_name(nozzle: str, pro: bool = False) -> str:
    if nozzle not in NOZZLES:
        raise ProfileError(f"Nozzle must be one of {', '.join(NOZZLES)} mm")
    return f"Flashforge Creator 5 {'Pro ' if pro else ''}{nozzle} nozzle"


def _suffix(pro: bool) -> str:
    return "@FF C5P" if pro else "@FF C5"


def process_presets(profiles_dir: Path, nozzle: str, pro: bool = False) -> dict[float, str]:
    """Map layer height (mm) to preset name for one nozzle size."""
    machine = machine_name(nozzle, pro)
    out: dict[float, str] = {}
    for (kind, name), data in _index(str(profiles_dir)).items():
        if kind != "process" or data.get("instantiation") != "true" or _suffix(pro) not in name:
            continue
        if pro is False and "@FF C5P" in name:
            continue
        flat = flatten(profiles_dir, "process", name)
        if machine not in (flat.get("compatible_printers") or []):
            continue
        match = re.match(r"([0-9.]+)mm", name)
        height = float(flat.get("layer_height") or (match.group(1) if match else 0))
        if height:
            out[height] = name
    return dict(sorted(out.items()))


def filament_presets(profiles_dir: Path, nozzle: str, pro: bool = False) -> dict[str, dict]:
    """Filament presets usable on the given nozzle, with headline facts."""
    machine = machine_name(nozzle, pro)
    suffix = _suffix(pro)
    out: dict[str, dict] = {}
    for (kind, name), data in _index(str(profiles_dir)).items():
        if kind != "filament" or data.get("instantiation") != "true" or not name.endswith(suffix):
            continue
        flat = flatten(profiles_dir, "filament", name)
        compatible = flat.get("compatible_printers") or []
        if compatible and machine not in compatible:
            continue
        out[name] = {
            "type": _first(flat.get("filament_type")),
            "nozzle_temperature": _first(flat.get("nozzle_temperature")),
            "bed_temperature": _first(flat.get("hot_plate_temp")),
            "max_volumetric_speed": _first(flat.get("filament_max_volumetric_speed")),
            "density": _first(flat.get("filament_density")),
        }
    return dict(sorted(out.items()))


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def apply_overrides(flat: dict, overrides: dict | None) -> tuple[dict, list[str]]:
    """Merge overrides, matching the JSON shape of the existing value.

    Returns the merged preset and a list of keys the preset did not already
    define (these are passed through, but flagged, since a typo would be
    silently ignored by the slicer).
    """
    merged = dict(flat)
    unknown: list[str] = []
    for key, value in (overrides or {}).items():
        existing = merged.get(key)
        if key not in merged:
            unknown.append(key)
        if isinstance(existing, list):
            if isinstance(value, list):
                merged[key] = [_to_str(v) for v in value]
            else:
                merged[key] = [_to_str(value)] * max(1, len(existing))
        elif isinstance(value, list):
            merged[key] = [_to_str(v) for v in value]
        else:
            merged[key] = _to_str(value)
    return merged, unknown


def _to_str(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def write_preset(preset: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(preset, indent=1), encoding="utf-8")
    return path
