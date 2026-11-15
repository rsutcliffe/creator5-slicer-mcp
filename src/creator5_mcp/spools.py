"""The owner's spools, saved once with their label figures.

"The black PLA" then resolves to the right filament, temperatures and speed
without retyping the label. The register is a small JSON file in the output folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILE_NAME = "spools.json"


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+]+", text.lower()))


def load(output_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((output_dir / FILE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(output_dir: Path, name: str, filament: str, nozzle_temp: int = 0, bed_temp: int = 0,
         max_speed: int = 0, colour: str = "", max_flow: float = 0) -> dict:
    """Add or replace a spool. `filament` is the material as recommend.match_material understands it.
    max_flow is a flow limit in mm3/s proven by a print or a flow test; it takes the place of max_speed."""
    name = " ".join(name.lower().split())
    if not name:
        raise ValueError("Give the spool a name, for example 'black pla'.")
    entry = {"filament": filament, "nozzle_temp": nozzle_temp, "bed_temp": bed_temp, "max_speed": max_speed,
             "max_flow": max_flow, "colour": colour}
    spools = load(output_dir)
    spools[name] = entry
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / FILE_NAME).write_text(json.dumps(spools, indent=2) + "\n", encoding="utf-8")
    return {"name": name, **entry}


def find(output_dir: Path, text: str) -> dict | None:
    """The saved spool whose every name word appears in `text`; the longest name wins."""
    said = _words(text)
    matches = [(len(_words(name)), name, entry) for name, entry in load(output_dir).items()
               if _words(name) and _words(name) <= said]
    if not matches:
        return None
    _, name, entry = max(matches, key=lambda m: (m[0], m[1]))
    return {"name": name, **entry}
