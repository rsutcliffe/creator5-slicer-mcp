"""Two test towers that change one thing with height: nozzle temperature, and pressure advance.

Flash Studio makes its own in its window and steps the value in memory, and its tower models are
packed files this server cannot read. These are plain shapes built from boxes, with the value
changed by G-code placed at the first layer of each block (write_project's gcode_at). Every print's
start G-code sets both the temperature and the pressure advance again, so a test stopped half-way
leaves nothing behind on the printer.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

LAYER = 0.2                 # every height below is a whole number of these, so each command lands on a layer
BASE = 0.6                  # a pad under the temperature tower, for grip
TEMP_BLOCK = 8.0            # mm of height for each temperature
POST, SPAN = 8.0, 22.0      # two posts this wide, this far apart: strings show between them, and a bridge joins them
PA_BLOCK = 4.0              # mm of height for each pressure advance value
PA_SIDE = 40.0              # the pressure advance tower is a square tube this wide: four sharp corners to read
MOST = 14                   # blocks in one tower


def _box(x: float, y: float, z: float, sx: float, sy: float, sz: float) -> list:
    v = [(x + dx, y + dy, z + dz) for dz in (0, sz) for dy in (0, sy) for dx in (0, sx)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return [t for a, b, c, d in quads for t in ((v[a], v[b], v[c]), (v[a], v[c], v[d]))]


def _steps(start: float, end: float, step: float) -> list[float]:
    if step <= 0 or start == end:
        raise ValueError("The test needs two different values and a step above 0.")
    count = int(round(abs(end - start) / step)) + 1
    if count > MOST:
        raise ValueError(f"That is {count} blocks, and a tower holds {MOST}. Use a larger step or a narrower range.")
    way = 1 if end > start else -1
    return [round(start + way * step * i, 4) for i in range(count)]


def temperature(hottest: int, coolest: int, step: int = 5) -> dict:
    """A temperature tower from `hottest` at the bottom to `coolest` at the top.

    Each block is two posts with a bridge across their tops. Returns "loaded" (the tower as a model),
    "values" (the temperature of each block from the bottom), "gcode_at" for write_project, the process
    and filament settings, and "block_mm".
    """
    if hottest <= coolest:
        raise ValueError("Give the hottest temperature first: the tower starts hot and cools as it rises.")
    values = [int(v) for v in _steps(hottest, coolest, step)]
    width = 2 * POST + SPAN
    tris = _box(-4, -4, 0, width + 8, POST + 8, BASE)
    for i in range(len(values)):
        low = BASE + i * TEMP_BLOCK
        tris += _box(0, 0, low, POST, POST, TEMP_BLOCK) + _box(POST + SPAN, 0, low, POST, POST, TEMP_BLOCK)
        tris += _box(0, 0, low + TEMP_BLOCK - 1.0, width, POST, 1.0)          # the bridge, printed at this block's temperature
    name = f"Temperature {values[0]} to {values[-1]} by {step}"
    gcode = [(round(BASE + i * TEMP_BLOCK + LAYER, 2), f"M104 S{value} ; temperature tower: block {i + 1}")
             for i, value in enumerate(values) if i]
    return {"loaded": _model(name, tris), "values": values, "gcode_at": gcode, "block_mm": TEMP_BLOCK, "base_mm": BASE,
            "process": {"layer_height": f"{LAYER:g}", "initial_layer_print_height": f"{LAYER:g}", "enable_support": "0",
                        "brim_type": "no_brim", "wall_loops": "2", "sparse_infill_density": "15%"},
            "filament": {"nozzle_temperature": str(values[0]), "nozzle_temperature_initial_layer": str(values[0])}}


def pressure_advance(start: float, end: float, step: float) -> dict:
    """A pressure advance tower from `start` at the bottom to `end` at the top: a square tube with a tick on its
    front face at the foot of each block. Returns the same keys as temperature()."""
    if end <= start or start < 0:
        raise ValueError("Pressure advance rises up the tower: give a start of 0 or more and a higher end.")
    values = _steps(start, end, step)
    tris = _box(0, 0, 0, PA_SIDE, PA_SIDE, len(values) * PA_BLOCK)
    for i in range(1, len(values)):
        tris += _box(4, -1.0, i * PA_BLOCK, 6, 1.5, 0.6)                       # a tick: count them up from the bed
    name = f"Pressure advance {values[0]:g} to {values[-1]:g} by {step:g}"
    gcode = [(round(i * PA_BLOCK + LAYER, 2), f"SET_PRESSURE_ADVANCE ADVANCE={value:g} ; pressure advance tower: block {i + 1}")
             for i, value in enumerate(values) if i]
    return {"loaded": _model(name, tris), "values": values, "gcode_at": gcode, "block_mm": PA_BLOCK, "base_mm": 0.0,
            "process": {"layer_height": f"{LAYER:g}", "initial_layer_print_height": f"{LAYER:g}", "enable_support": "0",
                        "wall_loops": "2", "top_shell_layers": "0", "bottom_shell_layers": "0", "sparse_infill_density": "0%",
                        "brim_type": "outer_only", "brim_width": "4", "wall_generator": "classic"},
            "filament": {"enable_pressure_advance": "1", "pressure_advance": f"{values[0]:g}"}}


def _model(name: str, tris: list) -> dict:
    part = {"name": name, "tris": np.array(tris, float), "extruder": 1, "paint": None}
    return {"objects": [{"name": name, "parts": [part]}], "colours": [], "filaments": 0, "notes": [], "plates": 1}


def commands(gcode: Path, word: str) -> list[tuple[float, str]]:
    """Every line of the print that starts with `word`, with the top of the layer it runs in: (mm, line).
    The slicer writes a layer's height, then any command placed for it. Lines before the first layer, which
    are the printer's own start-up, are left out."""
    found, height = [], None
    with gcode.open(errors="ignore") as handle:
        for line in handle:
            if line.startswith(";Z:"):
                height = float(line[3:])
            elif height is not None and line.startswith(word):
                found.append((height, line.split(";")[0].strip()))
    return found


def values_in(name: str, kind: str) -> list[float]:
    """The value of each block, from the tower's name in its project. kind is Temperature or Pressure advance."""
    match = re.fullmatch(rf"{kind} (\d+\.?\d*) to (\d+\.?\d*) by (\d+\.?\d*)", name)
    if not match:
        raise ValueError(f"That project is not a {kind.lower()} test this server wrote.")
    start, end, step = (float(g) for g in match.groups())
    return _steps(start, end, step)
