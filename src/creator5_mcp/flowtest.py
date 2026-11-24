"""A flow test: how much plastic a spool can melt each second, found by printing.

Flash Studio makes its own max volumetric speed test in its window and steps the speed in memory
(Plater::calib_max_vol_speed), so a project file cannot carry it. A height range that changes speed
would do the same job, but it crashes Flash Studio's command line (tried 24 Nov 2026; only a range
that changes layer height survives). So this writes a plate of short tubes with the same recipe as
Flash Studio's test, one wall of a wide thick line with the filament's flow cap lifted, and gives
each tube one flow through its own outer wall speed. Nothing is changed on the printer.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

_WORD = re.compile(r"([XYEF])(-?\d*\.?\d+)")

LINE_FACTOR = 1.75                  # line width as a multiple of the nozzle, as Flash Studio's test uses
LENGTH, WIDTH, HEIGHT = 60.0, 16.0, 12.0   # one tube in mm: straight sides long enough to reach the speed asked for
PITCH_X, PITCH_Y, COLUMNS = 72.0, 30.0, 3  # how the tubes are set out, with room for each one's brim
MOST = 15                           # tubes that fit the bed


def tube() -> np.ndarray:
    """A slot-shaped block, (n, 3, 3) triangles in mm. With one wall and no top, bottom or infill it prints as a tube."""
    radius, half = WIDTH / 2, (LENGTH - WIDTH) / 2
    ring = [(half + radius * math.cos(a), radius * math.sin(a)) for a in np.linspace(-math.pi / 2, math.pi / 2, 25)]
    ring += [(-half + radius * math.cos(a), radius * math.sin(a)) for a in np.linspace(math.pi / 2, 3 * math.pi / 2, 25)]
    tris = []
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        tris += [[(x0, y0, 0), (x1, y1, 0), (x1, y1, HEIGHT)], [(x0, y0, 0), (x1, y1, HEIGHT), (x0, y0, HEIGHT)],
                 [(0, 0, 0), (x1, y1, 0), (x0, y0, 0)], [(0, 0, HEIGHT), (x0, y0, HEIGHT), (x1, y1, HEIGHT)]]
    return np.array(tris, float)


def plate(nozzle: float, layer_height: float, start: float, end: float, step: float) -> dict:
    """The tubes and settings of a test from `start` to `end` mm3/s in steps of `step`.

    Returns "loaded" (the tubes as a model, slowest at the front left, rising left to right and then
    row by row to the back, each named for its flow and carrying its own wall speed), the process and
    filament settings for the whole plate, and "flows", the flow of each tube in order.
    """
    if not 0 < start < end or step <= 0:
        raise ValueError("The flow test needs 0 < start < end, and a step above 0.")
    flows = [round(start + step * i, 2) for i in range(int(round((end - start) / step)) + 1)]
    if len(flows) > MOST:
        raise ValueError(f"That is {len(flows)} tubes, and {MOST} fit the bed. Use a larger step or a narrower range.")
    line = round(nozzle * LINE_FACTOR, 2)
    area = line * layer_height                                  # mm2 of plastic laid for each mm travelled
    shape, objects = tube(), []
    for i, flow in enumerate(flows):
        row, column = divmod(i, COLUMNS)
        tris = shape + [column * PITCH_X, row * PITCH_Y, 0]
        objects.append({"name": f"Flow {flow:g}", "settings": {"outer_wall_speed": f"{flow / area:.1f}"},
                        "parts": [{"name": f"Flow {flow:g}", "tris": tris, "extruder": 1, "paint": None}]})
    process = {"wall_loops": "1", "top_shell_layers": "0", "bottom_shell_layers": "0", "sparse_infill_density": "0%",
               "layer_height": f"{layer_height:g}", "initial_layer_print_height": f"{layer_height:g}",
               "outer_wall_line_width": f"{line:g}", "initial_layer_line_width": f"{line:g}",
               "enable_overhang_speed": "0", "brim_type": "outer_only", "brim_width": "4", "brim_object_gap": "0",
               "max_volumetric_extrusion_rate_slope": "0", "enable_support": "0", "wall_generator": "classic"}
    filament = {"filament_max_volumetric_speed": "200", "slow_down_layer_time": "0"}
    return {"loaded": {"objects": objects, "colours": [], "filaments": 0, "notes": [], "plates": 1}, "process": process,
            "filament": filament, "flows": flows, "line_width_mm": line, "layer_height_mm": layer_height}


def measured(objects: list[dict], gcode: Path, area: float) -> list[float | None]:
    """The flow each tube really prints at, read from the G-code: its usual outer wall feedrate above the first
    millimetre, times the line's cross-section. Tubes are told apart by where they stand. None where a tube has no wall."""
    boxes = [(obj["parts"][0]["tris"].reshape(-1, 3).min(axis=0), obj["parts"][0]["tris"].reshape(-1, 3).max(axis=0)) for obj in objects]
    speeds: list[list[float]] = [[] for _ in objects]
    x = y = feed = height = 0.0
    feature = ""
    with gcode.open(errors="ignore") as handle:
        for line in handle:
            if line.startswith(";Z:"):
                height = float(line[3:])
            elif line.startswith(";TYPE:"):
                feature = line[6:].strip()
            elif line.startswith("G1 "):
                words = dict(_WORD.findall(line.split(";")[0]))
                feed = float(words.get("F", feed))
                nx, ny = float(words.get("X", x)), float(words.get("Y", y))
                if feature == "Outer wall" and height > 1.0 and float(words.get("E", 0)) > 0 and (nx != x or ny != y):
                    for i, (lo, hi) in enumerate(boxes):
                        if lo[0] - 1 <= nx <= hi[0] + 1 and lo[1] - 1 <= ny <= hi[1] + 1:
                            speeds[i].append(feed / 60)
                x, y = nx, ny
    return [round(sorted(found)[len(found) // 2] * area, 2) if found else None for found in speeds]


def flows_in(objects: list[dict]) -> list[float]:
    """The flow each tube of a flow-test project was written for, from its name."""
    found = [re.fullmatch(r"Flow (\d+\.?\d*)", obj["name"]) for obj in objects]
    if not found or not all(found):
        raise ValueError("That project is not a flow test this server wrote.")
    return [float(match.group(1)) for match in found]
