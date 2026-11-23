"""Does the G-code print what the project holds? Read from the toolpath, not the settings.

The other G-code checks confirm that each chosen setting took effect. These look at where the
plastic goes: a feature that starts with nothing under it, an object the slicer left out, a part
printed smaller than its model, a print that stops short. Flash Studio writes no per-object markers
(`exclude_object = 0`), so objects are told apart by where the project placed them.

Mid-air starts are found on a grid of half-millimetre cells over the bed, every layer. Outlines are
compared on quarter-millimetre cells, at a few heights, so detail down to about 0.3 mm is seen and
anything finer is not.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import model, slicer

CELL = 0.5                                  # mm per grid cell, for the mid-air search on every layer
FINE = 0.25                                 # mm per grid cell, for comparing outlines at a few heights
NOT_PART = ("support", "brim", "skirt", "prime tower", "wipe tower")   # printed, but not the part
UNDER_MM = 1.0                              # material within this distance on the layer below counts as support
MID_AIR_MM2 = 1.0                           # the smallest island worth reporting
EDGE_MM = 0.5                               # the model's outline is the band this deep inside its cross-section
SMALLER_MM2 = 1.0                           # outline with no line near it, in mm2 ...
SMALLER_SHARE = 0.10                        # ... and as a share of the outline, before it is worth reporting
SHORT_MM = 0.6                              # how far below the model's top the last layer may end
HEIGHTS = (0.25, 0.5, 0.75)                 # where, up a part's height, its cross-section is compared (and at the first layer)


def _raster(moves: list[tuple], cell: float = CELL, width: int = 1) -> np.ndarray:
    side = int(model.BED_X / cell)
    image = Image.new("L", (side, side), 0)
    draw = ImageDraw.Draw(image)
    for x0, y0, x1, y1 in moves:
        draw.line([(x0 / cell, y0 / cell), (x1 / cell, y1 / cell)], fill=255, width=width)
    return np.asarray(image) > 0


def _grow(mask: np.ndarray, cells: int, shrink: bool = False) -> np.ndarray:
    kind = ImageFilter.MinFilter if shrink else ImageFilter.MaxFilter
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(kind(2 * cells + 1))) > 0


def _box(mask: np.ndarray, margin: int = 3) -> tuple[slice, slice]:
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    return (slice(max(rows[0] - margin, 0), rows[-1] + margin + 1), slice(max(cols[0] - margin, 0), cols[-1] + margin + 1))


def _reach(seed: np.ndarray, within: np.ndarray) -> np.ndarray:
    """Everything in `within` that can be walked to from `seed`, a cell at a time."""
    reached = seed & within
    while True:
        wider = _grow(reached, 1) & within
        if (wider == reached).all():
            return reached
        reached = wider


def _section(tris: np.ndarray, z: float, cell: float = FINE) -> np.ndarray | None:
    """The filled cross-section of a mesh at height z, on the grid. None where the mesh is open there, so it
    has no inside to fill. Shells that overlap, as generated meshes often do, are filled as one."""
    side = int(model.BED_X / cell)
    out = np.zeros((side, side), bool)
    z += 1.234e-3                             # off any vertex, so every crossing triangle is cut in exactly two edges
    crossing = tris[(tris[:, :, 2].min(axis=1) < z) & (tris[:, :, 2].max(axis=1) > z)]
    if not len(crossing):
        return out
    normal = np.cross(crossing[:, 1] - crossing[:, 0], crossing[:, 2] - crossing[:, 0])
    ends = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        za, zb = crossing[:, a, 2], crossing[:, b, 2]
        cut = (za - z) * (zb - z) < 0
        t = np.where(cut, (z - za) / np.where(cut, zb - za, 1.0), 0.0)
        ends.append((cut, crossing[:, a, :2] + t[:, None] * (crossing[:, b, :2] - crossing[:, a, :2])))
    cuts = np.stack([e[0] for e in ends], axis=1)
    points = np.stack([e[1] for e in ends], axis=1)
    two = cuts.sum(axis=1) == 2
    order = np.argsort(~cuts[two], axis=1, kind="stable")[:, :2]            # the two edges that were cut
    seg = np.take_along_axis(points[two], order[:, :, None], axis=1) / cell  # (n, 2, 2), in cells
    # Each cut runs with the inside on its left, by its triangle's normal, so overlapping shells add up.
    along = seg[:, 1] - seg[:, 0]
    flip = along[:, 0] * -normal[two, 1] + along[:, 1] * normal[two, 0] < 0
    seg[flip] = seg[flip][:, ::-1]
    # A closed surface is cut into loops: every end of a cut meets the start of another.
    ends, times = np.unique(np.round(seg.reshape(-1, 2), 3), axis=0, return_counts=True)
    if (times % 2).mean() > 0.02:
        return None
    x0, y0, x1, y1 = seg[:, 0, 0], seg[:, 0, 1], seg[:, 1, 0], seg[:, 1, 1]
    lo, hi = max(int(min(y0.min(), y1.min())), 0), min(int(max(y0.max(), y1.max())) + 1, side)
    for row in range(lo, hi):
        y = row + 0.5
        hit = (y0 <= y) != (y1 <= y)
        if hit.any():
            xs = x0[hit] + (y - y0[hit]) * (x1[hit] - x0[hit]) / (y1[hit] - y0[hit])
            order = np.argsort(xs)
            inside = np.cumsum(np.where(y1[hit] > y0[hit], -1, 1)[order])   # how many surfaces we are inside, going right
            for left, right, deep in zip(xs[order], xs[order][1:], inside):
                if deep:
                    out[row, max(int(round(left)), 0):min(int(round(right)), side)] = True
    return out


def _where(objects: list[dict], x: float, y: float) -> str:
    for obj in objects:
        if obj["lo"][0] - 1 <= x <= obj["hi"][0] + 1 and obj["lo"][1] - 1 <= y <= obj["hi"][1] + 1:
            return obj["name"]
    return "no object"


def check(project: Path, gcode: Path) -> dict:
    """Compare the sliced G-code with the project it was sliced from.

    Returns mid_air (features that start with nothing under them), left_out (objects with no
    extrusion), smaller (parts printed smaller than modelled), stops_short, and the same findings
    in plain words as warnings.
    """
    began = time.time()
    loaded = model.load_objects(project)
    objects, wanted = [], []
    for obj in loaded["objects"]:
        points = np.concatenate([part["tris"].reshape(-1, 3) for part in obj["parts"]])
        entry = {"name": obj["name"], "lo": points.min(axis=0), "hi": points.max(axis=0)}
        objects.append(entry)
        tools = {}
        for part in obj["parts"]:
            tools.setdefault((part["extruder"] or 1) - 1, []).append(part["tris"])
        # The object as a whole against every toolhead, then each toolhead that prints a lesser share of it
        # (lettering, a cap) against its own lines. The main toolhead is not checked alone: its model may hold
        # the volume another part is set into.
        groups = [(None, [t for parts in tools.values() for t in parts])]
        if len(tools) > 1:
            size = {tool: sum(np.ptp(t.reshape(-1, 3), axis=0).prod() for t in parts) for tool, parts in tools.items()}
            groups += [(tool, parts) for tool, parts in tools.items() if tool != max(size, key=size.get)]
        for tool, parts in groups:
            pts = np.concatenate([t.reshape(-1, 3) for t in parts])
            low, high = float(pts[:, 2].min()), float(pts[:, 2].max())
            for share in (None, *HEIGHTS):
                wanted.append({"object": obj["name"], "tool": tool, "parts": parts, "first": share is None,
                               "z": low + (high - low) * (share or 0.0), "done": False})
    top = float(max(obj["hi"][2] for obj in objects))
    mid_air, smaller, open_meshes = [], [], set()
    ever = np.zeros((int(model.BED_X / CELL),) * 2, bool)
    below = None
    count, height, last = 0, 0.0, 0.0
    for count, height, moves in slicer.layers(gcode):
        part_moves = [m for m in moves if not any(word in m[5].lower() for word in NOT_PART)]
        part = _raster([m[:4] for m in part_moves])
        every = part | _raster([m[:4] for m in moves if any(word in m[5].lower() for word in NOT_PART)])
        ever |= part
        if below is not None and part.any():
            held = _grow(below, round(UNDER_MM / CELL))
            if (part & ~held).any():
                rows, cols = _box(every)
                here, joined = every[rows, cols], _grow(every[rows, cols], 1)
                standing = _reach(here & held[rows, cols], joined)          # held up, or joined in this layer to what is
                loose = part[rows, cols] & ~standing
                while loose.any():
                    first = np.zeros_like(loose)
                    first[tuple(np.argwhere(loose)[0])] = True
                    island = _reach(first, _grow(loose, 1)) & loose
                    loose &= ~island
                    area = float(island.sum()) * CELL * CELL
                    if area >= MID_AIR_MM2:
                        at = (np.argwhere(island).mean(axis=0) + [rows.start, cols.start]) * CELL
                        mid_air.append({"object": _where(objects, at[1], at[0]), "z": round(height, 2),
                                        "x": round(float(at[1]), 1), "y": round(float(at[0]), 1), "area_mm2": round(area, 1)})
        for want in wanted:
            layer_mid = height - (height - last) / 2
            if want["done"] or (not want["first"] and height < want["z"]) or (want["first"] and height <= want["z"]):
                continue
            want["done"] = True
            modelled = np.zeros((int(model.BED_X / FINE),) * 2, bool)
            sections = [_section(tris, layer_mid) for tris in want["parts"]]
            if any(section is None for section in sections):
                open_meshes.add(want["object"])
                continue
            for section in sections:
                modelled |= section
            if not modelled.any():
                continue
            # Walls follow the outline, so every part of the outline should have a line beside it. Sparse infill
            # leaves the inside open, so only the outline is compared.
            outline = modelled & ~_grow(modelled, round(EDGE_MM / FINE), shrink=True)
            lines = _raster([m[:4] for m in part_moves if want["tool"] is None or m[4] == want["tool"]], FINE, 2)
            missing = float((outline & ~_grow(lines, 1)).sum()) * FINE * FINE
            whole = float(outline.sum()) * FINE * FINE
            if missing >= SMALLER_MM2 and missing >= SMALLER_SHARE * whole:
                smaller.append({"object": want["object"], "toolhead": None if want["tool"] is None else want["tool"] + 1,
                                "z": round(height, 2), "outline_mm2": round(whole, 1), "missing_mm2": round(missing, 1)})
        below, last = every, height
    left_out = []
    for obj in objects:
        rows = slice(int(obj["lo"][1] / CELL), int(obj["hi"][1] / CELL) + 1)
        cols = slice(int(obj["lo"][0] / CELL), int(obj["hi"][0] / CELL) + 1)
        if not ever[rows, cols].any():
            left_out.append(obj["name"])
    short = round(top - height, 2) if top - height > SHORT_MM else None
    warnings = [f"'{f['object']}' has {f['area_mm2']:g} mm² that starts printing in mid-air at {f['z']:g} mm "
                f"(x {f['x']:g}, y {f['y']:g}): nothing is printed under it and it joins nothing on its own layer. "
                "Turn supports on, or turn the part." for f in mid_air[:8]]
    if len(mid_air) > 8:
        warnings.append(f"{len(mid_air) - 8} more places start in mid-air; see sliced_check.mid_air.")
    warnings += [f"'{name}' is in the project but the G-code prints nothing where it stands." for name in left_out]
    seen = set()
    for f in smaller:
        key = (f["object"], f["toolhead"])
        if key not in seen:                   # one warning for each object and toolhead, at its first failing height
            seen.add(key)
            who = f"'{f['object']}'" + (f", toolhead {f['toolhead']}" if f["toolhead"] else "")
            warnings.append(f"{who} prints smaller than its model at {f['z']:g} mm: {f['missing_mm2']:g} of {f['outline_mm2']:g} mm² "
                            "of its outline has no line beside it. The slicer drops detail too thin to print, and a part given "
                            "the wrong toolhead prints in another colour.")
    if short:
        warnings.append(f"The print ends at {height:g} mm, {short:g} mm below the model's top at {top:g} mm.")
    not_judged = [f"'{name}' is an open mesh at one or more heights, so its outline could not be compared with what prints."
                  for name in sorted(open_meshes)]
    return {"checked": True, "not_judged": not_judged, "layers": count, "top_z": round(height, 2), "mid_air": mid_air, "left_out": left_out,
            "smaller": smaller, "stops_short_mm": short, "warnings": warnings, "seconds": round(time.time() - began, 1)}
