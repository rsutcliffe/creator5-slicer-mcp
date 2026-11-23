"""Pictures of what was made: the plate as the project holds it, and one sliced layer from above.

Flash Studio's command line cannot draw (it has no OpenGL without a window), so these are drawn
here with numpy and Pillow. `_view`, `_rasterize` and `_paint_slot` are adapted from
unofficial-bambu-mcp's preview.py, Copyright (c) 2026 Daniele Finocchiaro, MIT licence: see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import model

BACKGROUND = (36, 38, 42)
BED_COLOUR = (70, 74, 80)
UNKNOWN = "#B0B0B0"                              # a toolhead the project gives no colour for
PALE = ("support", "brim", "skirt")              # features that are not the part, drawn paler
LIGHT = np.array([-0.35, -0.45, 0.82])           # from the front left, above


def _rgb(colour: str) -> np.ndarray:
    text = (colour or UNKNOWN).lstrip("#")[:6].ljust(6, "0")
    return np.array([int(text[i:i + 2], 16) for i in (0, 2, 4)], float)


def _view(points: np.ndarray, view: str) -> np.ndarray:
    """World mm -> (u, v, depth). Larger depth is nearer the viewer."""
    p = np.asarray(points, float)
    if view == "top":
        return p.copy()
    az, el = math.radians(-35), math.radians(55)       # from the front left, looking down
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    xr = x * math.cos(az) - y * math.sin(az)
    yr = x * math.sin(az) + y * math.cos(az)
    return np.column_stack([xr, yr * math.cos(el) + z * math.sin(el), -yr * math.sin(el) + z * math.cos(el)])


def _rasterize(px: np.ndarray, z: np.ndarray, rgb: np.ndarray, width: int, height: int, budget: int = 2_000_000) -> np.ndarray:
    """Z-buffer: each pixel takes the colour of the nearest triangle. Sorting triangles by their centre
    fails where big faces lie under small ones (lettering on a tag), so depth is kept per pixel.
    px: (n, 3, 2) pixel coordinates, z: (n, 3) depths, rgb: (n, 3). Returns (height, width, 3) with
    untouched pixels left at the background. budget caps the pixels worked on at once, so memory stays flat."""
    zbuf = np.full(width * height, -np.inf)
    cbuf = np.tile(np.array(BACKGROUND, np.uint8), (width * height, 1))
    (x0, y0), (x1, y1), (x2, y2) = px[:, 0].T, px[:, 1].T, px[:, 2].T
    a2 = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    ok = np.abs(a2) > 1e-9
    lo = np.clip(np.floor(px.min(1)), 0, [width - 1, height - 1]).astype(np.int64)
    hi = np.clip(np.ceil(px.max(1)), 0, [width - 1, height - 1]).astype(np.int64)
    ok &= (px[:, :, 0].max(1) >= 0) & (px[:, :, 0].min(1) < width) & (px[:, :, 1].max(1) >= 0) & (px[:, :, 1].min(1) < height)
    a2 = np.where(ok, a2, 1.0)
    # barycentric weights as planes in pixel space: w0 = A0 x + B0 y + C0, w1 likewise, w2 = 1 - w0 - w1
    A0, B0, C0 = (y1 - y2) / a2, (x2 - x1) / a2, (x1 * y2 - x2 * y1) / a2
    A1, B1, C1 = (y2 - y0) / a2, (x0 - x2) / a2, (x2 * y0 - x0 * y2) / a2
    dz0, dz1 = z[:, 0] - z[:, 2], z[:, 1] - z[:, 2]
    Dz, Ez, Fz = A0 * dz0 + A1 * dz1, B0 * dz0 + B1 * dz1, z[:, 2] + C0 * dz0 + C1 * dz1
    tris = np.nonzero(ok)[0]
    w = hi[tris, 0] - lo[tris, 0] + 1
    n = w * (hi[tris, 1] - lo[tris, 1] + 1)
    ends = np.cumsum(n)
    i = 0
    while i < len(tris):
        j = max(i + 1, int(np.searchsorted(ends, (ends[i - 1] if i else 0) + budget, "right")))
        cnt = n[i:j]
        t = np.repeat(tris[i:j], cnt)
        off = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        ww = np.repeat(w[i:j], cnt)
        xs = lo[t, 0] + off % ww
        ys = lo[t, 1] + off // ww
        cx, cy = xs + 0.5, ys + 0.5
        b0 = A0[t] * cx + B0[t] * cy + C0[t]
        b1 = A1[t] * cx + B1[t] * cy + C1[t]
        inside = (b0 >= -1e-6) & (b1 >= -1e-6) & (b0 + b1 <= 1 + 1e-6)
        pix = (ys * width + xs)[inside]
        d = (Dz[t] * cx + Ez[t] * cy + Fz[t])[inside]
        t = t[inside]
        order = np.lexsort((d, pix))                    # by pixel, then depth: the last of each pixel is nearest
        pix, d, t = pix[order], d[order], t[order]
        last = np.r_[pix[1:] != pix[:-1], True]
        pix, d, t = pix[last], d[last], t[last]
        near = d > zbuf[pix]
        zbuf[pix[near]] = d[near]
        cbuf[pix[near]] = rgb[t[near]]
        i = j
    return cbuf.reshape(height, width, 3)


def _paint_slot(code: str) -> int | None:
    """The 1-based toolhead a painted triangle is given, from Bambu/Orca's paint_color string, read from
    its last character: the two low bits say the triangle is split, the two high bits its state, and state 3
    means the character before it plus 3. '4' is 1, '8' is 2, '0C' is 3, '1C' is 4.
    None for an unpainted triangle and for one split into smaller painted pieces."""
    try:
        last = int(code[-1], 16)
        if last & 3:
            return None
        state = last >> 2
        if state == 3:
            state = int(code[-2], 16) + 3
        return state or None
    except (ValueError, IndexError):
        return None


def _png(pixels: np.ndarray, size: int) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(pixels).resize((size, size), Image.LANCZOS).save(buffer, "PNG")
    return buffer.getvalue()


def plate_png(project: Path, view: str = "iso", size: int = 900) -> tuple[bytes, list[str]]:
    """The plate as the project file holds it: the 256 mm bed and every part in its toolhead's colour.

    view is "iso" (from the front left, looking down) or "top". Returns the PNG and any notes about
    what could not be drawn as it will print.
    """
    if view not in ("iso", "top"):
        raise model.ModelError("view must be iso or top")
    loaded = model.load_objects(project)
    colours = loaded["colours"]
    # A black filament would vanish against the dark bed, so no colour is drawn darker than a deep grey.
    palette = np.array([np.maximum(_rgb(colours[s] if s < len(colours) and colours[s] else UNKNOWN), 52) for s in range(16)])
    tris, shades, split = [], [], 0
    for obj in loaded["objects"]:
        for part in obj["parts"]:
            slot = np.full(len(part["tris"]), part["extruder"] or 1)
            for i, code in enumerate(part["paint"] or []):
                if code:
                    painted = _paint_slot(code)
                    split += painted is None
                    slot[i] = painted or slot[i]
            tris.append(part["tris"])
            shades.append(palette[np.clip(slot, 1, 16) - 1])
    tris, base = np.concatenate(tris), np.concatenate(shades)
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normal, axis=1)
    normal = normal / np.where(length > 0, length, 1.0)[:, None]
    lit = 0.5 + 0.5 * np.clip(normal @ (LIGHT / np.linalg.norm(LIGHT)), 0, 1)
    rgb = np.clip(base * lit[:, None], 0, 255)
    bed = np.array([[[0, 0, 0], [model.BED_X, 0, 0], [model.BED_X, model.BED_Y, 0]],
                    [[0, 0, 0], [model.BED_X, model.BED_Y, 0], [0, model.BED_Y, 0]]], float)
    bed[:, :, 2] -= 0.05                                   # just under the parts, so the first layer shows on it
    tris, rgb = np.concatenate([bed, tris]), np.concatenate([np.tile(BED_COLOUR, (2, 1)), rgb])
    seen = _view(tris.reshape(-1, 3), view)
    scale = 2 * size * 0.92 / max(np.ptp(seen[:, 0]), np.ptp(seen[:, 1]))
    centre = (seen[:, :2].min(0) + seen[:, :2].max(0)) / 2
    px = np.empty((len(seen), 2))
    px[:, 0] = size + (seen[:, 0] - centre[0]) * scale
    px[:, 1] = size - (seen[:, 1] - centre[1]) * scale     # image rows run downwards
    pixels = _rasterize(px.reshape(-1, 3, 2), seen[:, 2].reshape(-1, 3), rgb.astype(np.uint8), 2 * size, 2 * size)
    notes = [f"{split} colour-painted triangles are split into smaller painted pieces; the picture shows them in "
             "their part's colour, not the painted one."] if split else []
    return _png(pixels, size), notes


def layer_png(moves: list[tuple], colours: list[str], title: str = "", size: int = 900) -> bytes:
    """One sliced layer from above, framed on what is printed: each extrusion in its toolhead's colour,
    supports, brim and skirt paler. moves are (x0, y0, x1, y1, toolhead, feature), as slicer.layer_moves gives them."""
    S = 2
    side = size * S
    image = Image.new("RGB", (side, side), BACKGROUND)
    draw = ImageDraw.Draw(image)
    xy = np.array([m[:4] for m in moves], float).reshape(-1, 4)
    if len(xy):
        lo = np.minimum(xy[:, :2].min(0), xy[:, 2:].min(0)) - 3
        hi = np.maximum(xy[:, :2].max(0), xy[:, 2:].max(0)) + 3
    else:
        lo, hi = np.zeros(2), np.array([model.BED_X, model.BED_Y])
    scale = side * 0.94 / max(hi - lo)
    mid = (lo + hi) / 2
    at = lambda x, y: (side / 2 + (x - mid[0]) * scale, side / 2 - (y - mid[1]) * scale)
    draw.rectangle([at(0, model.BED_Y), at(model.BED_X, 0)], fill=BED_COLOUR)
    width = max(1, round(0.42 * scale))
    for x0, y0, x1, y1, tool, feature in moves:
        colour = np.maximum(_rgb(colours[tool] if tool < len(colours) and colours[tool] else UNKNOWN), 52)
        if any(word in feature.lower() for word in PALE):
            colour = colour * 0.4 + 255 * 0.6 if colour.mean() < 128 else colour * 0.55
        draw.line([at(x0, y0), at(x1, y1)], fill=tuple(int(v) for v in colour), width=width)
    image = image.resize((size, size), Image.LANCZOS)
    if title:
        ImageDraw.Draw(image).text((12, 10), title, fill=(230, 230, 230))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
