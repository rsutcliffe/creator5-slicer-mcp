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
def _index(profiles_dir: str) -> dict[tuple[str, str, str], dict]:
    """Every preset, keyed by (kind, vendor, name).

    The vendor is part of the key because the same name can exist in two
    vendor folders with different contents. Flash Studio, for example, has an
    old `fdm_filament_common` under Flashforge (2.85 mm filament) and the
    current one under OrcaFilamentLibrary (1.75 mm).
    """
    index: dict[tuple[str, str, str], dict] = {}
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
                if name and (kind, vendor, name) not in index:
                    index[(kind, vendor, name)] = data
    if not index:
        raise ProfileError(f"No presets found under {profiles_dir}")
    return index


def _lookup(index: dict, kind: str, name: str, prefer: str) -> tuple[str, dict] | None:
    for vendor in (prefer, *[v for v in VENDORS if v != prefer]):
        data = index.get((kind, vendor, name))
        if data is not None:
            return vendor, data
    return None


def _presets(profiles_dir: Path, kind: str) -> dict[str, dict]:
    """Presets of one kind as the user sees them: Flashforge first, then the shared library."""
    out: dict[str, dict] = {}
    for vendor in VENDORS:
        for (k, v, name), data in _index(str(profiles_dir)).items():
            if k == kind and v == vendor:
                out.setdefault(name, data)
    return out


def flatten(profiles_dir: Path, kind: str, name: str) -> dict:
    """Return preset `name` with every inherited value merged in.

    A parent is looked for in the same vendor folder as the preset that names
    it, and only then in the other folder. This is how the slicer resolves
    inheritance, and it keeps a preset in the shared filament library from
    picking up a same-named but different parent in the Flashforge folder.
    """
    index = _index(str(profiles_dir))
    chain: list[dict] = []
    seen: set[tuple[str, str]] = set()
    current: str | None = name
    vendor = VENDORS[0]
    while current:
        found = _lookup(index, kind, current, vendor)
        if found is None:
            raise ProfileError(f"{kind} preset '{current}' not found (needed by '{name}')")
        vendor, data = found
        if (vendor, current) in seen:
            raise ProfileError(f"Circular inheritance at {kind} preset '{current}'")
        seen.add((vendor, current))
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
    for name, data in _presets(profiles_dir, "process").items():
        if data.get("instantiation") != "true" or _suffix(pro) not in name:
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
    for name, data in _presets(profiles_dir, "filament").items():
        if data.get("instantiation") != "true" or not name.endswith(suffix):
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
            "diameter": _first(flat.get("filament_diameter")),
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
