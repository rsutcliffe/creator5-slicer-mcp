"""Read a model (STL, OBJ or 3MF) and measure it so settings can respond to the part's shape."""

from __future__ import annotations

import json
import math
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

BED_X = BED_Y = BED_Z = 256.0  # Creator 5 build volume in mm, from the Orca machine preset
MESH_SUFFIXES = (".stl", ".obj", ".3mf")
DEFAULT_SUPPORT_ANGLE = 30.0   # support_threshold_angle in every Creator 5 process preset

_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
_PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
_UNITS = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}

# Designer choices worth keeping from a 3MF that carries slicer settings: they depend on the
# part's shape, which the designer has usually test-printed. Everything else comes from us.
DESIGNER_KEYS = ("enable_support", "support_type", "support_style", "support_threshold_angle",
                 "support_on_build_plate_only", "brim_type", "brim_width", "brim_object_gap", "raft_layers")


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


def _load_obj(path: Path) -> np.ndarray:
    verts: list[list[float]] = []
    tris: list[list[int]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            verts.append([float(p) for p in parts[1:4]])
        elif parts[0] == "f" and len(parts) >= 4:
            idx = [int(p.split("/")[0]) for p in parts[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
            tris += [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]   # fan-triangulate polygons
    if not verts or not tris:
        raise ModelError(f"{path.name} is not a readable OBJ")
    return np.array(verts, dtype=np.float64)[np.array(tris)]


def _matrix(text: str | None) -> np.ndarray:
    """3MF transform "m00 m01 m02 m10 ... m32": a 4x3 matrix applied to row vectors."""
    if not text:
        return np.vstack([np.eye(3), np.zeros(3)])
    return np.array([float(v) for v in text.split()], dtype=np.float64).reshape(4, 3)


def _apply(tris: np.ndarray, m: np.ndarray) -> np.ndarray:
    return tris @ m[:3] + m[3]


def _load_3mf(path: Path) -> tuple[np.ndarray, list[str]]:
    """Triangles of the parts on the first plate, placed as the designer left them.

    Follows the production extension (components kept in separate model files, as
    Bambu Studio and OrcaSlicer write them) and leaves out modifier, negative and
    support-blocker parts, which are not printed.
    """
    notes: list[str] = []
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ModelError(f"{path.name} is not a readable 3MF") from exc
    with archive:
        names = set(archive.namelist())
        root_name = "3D/3dmodel.model"
        if "_rels/.rels" in names:
            for rel in ET.fromstring(archive.read("_rels/.rels")):
                if rel.get("Type", "").endswith("/3dmodel"):
                    root_name = rel.get("Target", root_name).lstrip("/")
        trees: dict[str, ET.Element] = {}

        def tree(name: str) -> ET.Element:
            if name not in trees:
                if name not in names:
                    raise ModelError(f"{path.name} refers to {name}, which is missing from the file")
                trees[name] = ET.fromstring(archive.read(name))
            return trees[name]

        # Bambu/Orca record part types and plates in model_settings.config.
        skip: set[tuple[str, str]] = set()
        plate_objects: list[str] | None = None
        if "Metadata/model_settings.config" in names:
            cfg = ET.fromstring(archive.read("Metadata/model_settings.config"))
            for obj in cfg.findall("object"):
                for part in obj.findall("part"):
                    if part.get("subtype", "normal_part") != "normal_part":
                        skip.add((obj.get("id"), part.get("id")))
            plates = cfg.findall("plate")
            if plates:
                plate_objects = [m.get("value") for inst in plates[0].findall("model_instance")
                                 for m in inst.findall("metadata") if m.get("key") == "object_id"]
                if len(plates) > 1:
                    notes.append(f"The file holds {len(plates)} plates; only plate 1 was used.")

        root = tree(root_name)
        scale = _UNITS.get(root.get("unit", "millimeter"), 1.0)

        def object_tris(file: str, oid: str, depth: int = 0) -> np.ndarray:
            if depth > 8:
                raise ModelError(f"{path.name} nests components too deeply")
            obj = next((o for o in tree(file).iter(_CORE + "object") if o.get("id") == oid), None)
            if obj is None:
                raise ModelError(f"{path.name}: object {oid} not found in {file}")
            mesh = obj.find(_CORE + "mesh")
            if mesh is not None:
                if obj.get("type", "model") not in ("model", "solidsupport"):
                    return np.empty((0, 3, 3))
                v = np.array([[float(e.get("x")), float(e.get("y")), float(e.get("z"))]
                              for e in mesh.iter(_CORE + "vertex")], dtype=np.float64)
                t = np.array([[int(e.get("v1")), int(e.get("v2")), int(e.get("v3"))]
                              for e in mesh.iter(_CORE + "triangle")], dtype=np.int64)
                return v[t] if len(t) else np.empty((0, 3, 3))
            parts = []
            for comp in obj.iter(_CORE + "component"):
                cid = comp.get("objectid")
                if (oid, cid) in skip:
                    continue
                sub = comp.get(_PROD + "path", "").lstrip("/") or file
                parts.append(_apply(object_tris(sub, cid, depth + 1), _matrix(comp.get("transform"))))
            return np.concatenate(parts) if parts else np.empty((0, 3, 3))

        placed = []
        build = root.find(_CORE + "build")
        for item in (build if build is not None else []):
            oid = item.get("objectid")
            if item.get("printable", "1") == "0" or (plate_objects is not None and oid not in plate_objects):
                continue
            placed.append(_apply(object_tris(root_name, oid), _matrix(item.get("transform"))))
        if len(placed) > 1:
            notes.append(f"{len(placed)} objects were joined into one part, keeping their positions.")
    tris = np.concatenate(placed) if placed else np.empty((0, 3, 3))
    if not len(tris):
        raise ModelError(f"{path.name} holds no printable mesh")
    return tris * scale, notes


def load_mesh(path: Path) -> tuple[np.ndarray, list[str]]:
    """Triangles (n, 3, 3) in millimetres, oriented as the file has them, plus any notes."""
    if not path.is_file():
        raise ModelError(f"Model not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".stl":
        return _load_stl(path), []
    if suffix == ".obj":
        return _load_obj(path), []
    if suffix == ".3mf":
        return _load_3mf(path)
    raise ModelError(f"{path.suffix} files cannot be read here. Export the model as STL or 3MF.")


def designer_settings(path: Path) -> dict:
    """Shape-dependent settings saved in a 3MF by the slicer it was designed in, if any."""
    if path.suffix.lower() != ".3mf":
        return {}
    try:
        with zipfile.ZipFile(path) as archive:
            data = json.loads(archive.read("Metadata/project_settings.config"))
    except (KeyError, ValueError, zipfile.BadZipFile, OSError):
        return {}
    out = {}
    for key in DESIGNER_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value not in (None, ""):
            out[key] = str(value)
    return out


def analyse(path: Path, support_angle: float = DEFAULT_SUPPORT_ANGLE) -> dict:
    """Return size, volume and printability hints for a model file.

    support_angle is the slicer's support_threshold_angle: a downward face flatter
    than this, measured from the horizontal, needs support.
    """
    if not path.is_file():
        raise ModelError(f"Model not found: {path}")
    if path.suffix.lower() not in MESH_SUFFIXES:
        return {"file": str(path), "analysed": False,
                "note": "Shape analysis covers STL, OBJ and 3MF. Export the model as one of those."}
    tris, notes = load_mesh(path)
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
    # Downward faces flatter than the support angle, and not resting on the bed.
    overhang = (nz < -math.cos(math.radians(support_angle))) & (zmin_tri - lo[2] > 0.3)
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
        "support_angle": support_angle,
        "overhang_area_mm2": round(overhang_area, 1),
        "overhang_fraction": round(overhang_area / total_area, 4) if total_area else 0.0,
        "height_to_footprint": round(float(size[2]) / footprint_min, 2) if footprint_min else 0.0,
        "fits_bed": bool(size[0] <= BED_X and size[1] <= BED_Y and size[2] <= BED_Z),
        "fits_bed_if_rotated": bool(sorted(size)[0] <= BED_Z and sorted(size)[1] <= BED_X and sorted(size)[2] <= BED_X),
        "notes": notes,
    }
