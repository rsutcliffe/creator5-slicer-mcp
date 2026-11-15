"""Measure an STL so settings can respond to the part's shape."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

BED_X = BED_Y = BED_Z = 256.0  # Creator 5 build volume in mm, from the Orca machine preset


class ModelError(RuntimeError):
    pass


def _load_stl(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    if len(raw) >= 84:
        (count,) = struct.unpack_from("<I", raw, 80)
        if 84 + count * 50 == len(raw):
            dtype = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
            return np.frombuffer(raw, dtype=dtype, count=count, offset=84)["v"].astype(np.float64)
    verts: list[list[float]] = []
    for line in raw.decode("utf-8", "ignore").splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            verts.append([float(p) for p in parts[1:]])
    if not verts or len(verts) % 3:
        raise ModelError(f"{path.name} is not a readable STL")
    return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)


def analyse(path: Path) -> dict:
    """Return size, volume and printability hints for a model file."""
    if not path.is_file():
        raise ModelError(f"Model not found: {path}")
    if path.suffix.lower() != ".stl":
        return {"file": str(path), "analysed": False,
                "note": "Shape analysis covers STL only. The slicer still accepts 3MF, STEP and OBJ."}
    tris = _load_stl(path)
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    size = hi - lo
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    with np.errstate(invalid="ignore", divide="ignore"):
        nz = np.where(area > 0, cross[:, 2] / (2 * area), 0.0)
    volume = abs(float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum()) / 6)
    zmin_tri = tris[:, :, 2].min(axis=1)
    on_bed = (zmin_tri - lo[2] < 0.05) & (nz < -0.99)
    contact = float(area[on_bed].sum())
    # Faces pointing down more steeply than 45 degrees, and not resting on the bed.
    overhang = (nz < -0.7071) & (zmin_tri - lo[2] > 0.3)
    overhang_area = float(area[overhang].sum())
    total_area = float(area.sum())
    footprint_min = float(min(size[0], size[1]))
    return {
        "file": str(path),
        "analysed": True,
        "triangles": int(len(tris)),
        "size_mm": [round(float(s), 2) for s in size],
        "volume_cm3": round(volume / 1000, 2),
        "bed_contact_area_mm2": round(contact, 1),
        "overhang_area_mm2": round(overhang_area, 1),
        "overhang_fraction": round(overhang_area / total_area, 4) if total_area else 0.0,
        "height_to_footprint": round(float(size[2]) / footprint_min, 2) if footprint_min else 0.0,
        "fits_bed": bool(size[0] <= BED_X and size[1] <= BED_Y and size[2] <= BED_Z),
        "fits_bed_if_rotated": bool(sorted(size)[0] <= BED_Z and sorted(size)[1] <= BED_X and sorted(size)[2] <= BED_X),
    }
