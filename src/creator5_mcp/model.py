"""Read a model (STL, OBJ or 3MF) and measure it so settings can respond to the part's shape."""

from __future__ import annotations

import importlib.util
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

BED_X = BED_Y = BED_Z = 256.0  # Creator 5 build volume in mm, from the Orca machine preset
CAD_SUFFIXES = (".step", ".stp")
MESH_SUFFIXES = (".stl", ".obj", ".3mf", *CAD_SUFFIXES)
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


def clean_name(text: str) -> str:
    """A part name without the file-type tail slicers add, e.g. 'holder v3.stl_1' -> 'holder v3'."""
    return re.sub(r"\.(stl|3mf|obj|step|stp)(_\d+)?$", "", text.strip(), flags=re.I).strip()


def _colour(text: str | None) -> str | None:
    """'#RRGGBBAA' or '#RRGGBB' as '#RRGGBB'."""
    return text[:7].upper() if text and re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", text) else None


def _load_3mf(path: Path) -> dict:
    """The objects on the first plate, placed as the designer left them.

    Returns {"objects": [{"name", "parts": [{"name", "tris", "extruder", "paint"}]}],
             "colours": [...], "filaments": n, "notes": [...]}.
    extruder is the 1-based toolhead the file assigns the part to, or None. paint is the
    per-triangle colour painting (Bambu/Orca `paint_color`), or None. colours are the
    file's filament colours in toolhead order, when it records them.

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

        # Bambu/Orca record part types, names, toolheads and plates in model_settings.config.
        skip: set[tuple[str, str]] = set()
        cfg_name: dict[tuple[str, str | None], str] = {}
        cfg_extruder: dict[tuple[str, str | None], int] = {}
        plate_objects: list[str] | None = None
        if "Metadata/model_settings.config" in names:
            cfg = ET.fromstring(archive.read("Metadata/model_settings.config"))
            for obj in cfg.findall("object"):
                for owner, key in [(obj, (obj.get("id"), None))] + [(part, (obj.get("id"), part.get("id"))) for part in obj.findall("part")]:
                    for meta in owner.findall("metadata"):
                        if meta.get("key") == "name" and meta.get("value"):
                            cfg_name[key] = meta.get("value")
                        elif meta.get("key") == "extruder" and (meta.get("value") or "0").isdigit() and int(meta.get("value")) > 0:
                            cfg_extruder[key] = int(meta.get("value"))
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

        # Colours: a plain 3MF lists them as base materials; Bambu/Orca in the project settings.
        materials: dict[str, list[str | None]] = {}
        for group in root.iter(_CORE + "basematerials"):
            materials[group.get("id")] = [_colour(base.get("displaycolor")) for base in group.findall(_CORE + "base")]
        colours: list[str | None] = next(iter(materials.values()), [])
        filaments = len(colours)
        if "Metadata/project_settings.config" in names:
            try:
                saved = json.loads(archive.read("Metadata/project_settings.config")).get("filament_colour") or []
                colours, filaments = [_colour(c) for c in saved], len(saved)
            except ValueError:
                pass

        def parts_of(file: str, oid: str, top: str, inherited: int | None, depth: int = 0) -> list[dict]:
            if depth > 8:
                raise ModelError(f"{path.name} nests components too deeply")
            obj = next((o for o in tree(file).iter(_CORE + "object") if o.get("id") == oid), None)
            if obj is None:
                raise ModelError(f"{path.name}: object {oid} not found in {file}")
            extruder = cfg_extruder.get((top, oid)) or inherited
            if obj.get("pid") in materials and obj.get("pindex", "").isdigit():
                extruder = extruder or int(obj.get("pindex")) + 1
            mesh = obj.find(_CORE + "mesh")
            if mesh is not None:
                if obj.get("type", "model") not in ("model", "solidsupport"):
                    return []
                v = np.array([[float(e.get("x")), float(e.get("y")), float(e.get("z"))]
                              for e in mesh.iter(_CORE + "vertex")], dtype=np.float64)
                triangles = list(mesh.iter(_CORE + "triangle"))
                if not triangles:
                    return []
                t = np.array([[int(e.get("v1")), int(e.get("v2")), int(e.get("v3"))] for e in triangles], dtype=np.int64)
                if extruder is None and triangles[0].get("pid") in materials and (triangles[0].get("p1") or "").isdigit():
                    extruder = int(triangles[0].get("p1")) + 1
                paint = [e.get("paint_color", "") for e in triangles]
                return [{"name": clean_name(cfg_name.get((top, oid)) or obj.get("name") or ""), "tris": v[t],
                         "extruder": extruder, "paint": paint if any(paint) else None}]
            out = []
            for comp in obj.iter(_CORE + "component"):
                cid = comp.get("objectid")
                if (top, cid) in skip:
                    continue
                sub = comp.get(_PROD + "path", "").lstrip("/") or file
                m = _matrix(comp.get("transform"))
                for part in parts_of(sub, cid, top, extruder, depth + 1):
                    out.append({**part, "tris": _apply(part["tris"], m)})
            return out

        objects = []
        build = root.find(_CORE + "build")
        for item in (build if build is not None else []):
            oid = item.get("objectid")
            if item.get("printable", "1") == "0" or (plate_objects is not None and oid not in plate_objects):
                continue
            m = _matrix(item.get("transform"))
            parts = [{**part, "tris": _apply(part["tris"], m) * scale}
                     for part in parts_of(root_name, oid, oid, cfg_extruder.get((oid, None)))]
            if parts:
                top = next((o for o in root.iter(_CORE + "object") if o.get("id") == oid), None)
                name = clean_name(cfg_name.get((oid, None)) or (top.get("name") if top is not None else "") or "")
                objects.append({"name": name or parts[0]["name"], "parts": parts})
    if not objects:
        raise ModelError(f"{path.name} holds no printable mesh")
    return {"objects": objects, "colours": colours, "filaments": filaments, "notes": notes}


def convert_step(path: Path, out_dir: Path) -> Path:
    """Mesh a STEP file into an OBJ in out_dir, in millimetres and the file's own orientation.

    Neither Flash Studio's nor OrcaSlicer's command line reads STEP, so the optional `cascadio`
    library (OpenCASCADE) does it. It runs in a separate process: the library prints to standard
    output, which would corrupt the MCP connection if it ran inside the server.
    """
    if importlib.util.find_spec("cascadio") is None:
        raise ModelError("STEP files need the optional converter. Install it with: pip install cascadio "
                         "(or re-run scripts/setup.sh). Otherwise open the file in Flash Studio and export it as STL.")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{clean_name(path.stem)}.obj"
    code = "import sys, cascadio; sys.exit(cascadio.step_to_obj(sys.argv[1], sys.argv[2], tol_linear=0.01, tol_angular=0.5, use_colors=False))"
    try:
        result = subprocess.run([sys.executable, "-c", code, str(path), str(out)], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise ModelError(f"Converting {path.name} took longer than 5 minutes and was stopped.") from exc
    if result.returncode != 0 or not out.is_file() or not out.stat().st_size:
        out.unlink(missing_ok=True)
        if not any(out_dir.iterdir()):
            out_dir.rmdir()      # do not leave an empty folder behind in the prints folder
        detail = (result.stdout + result.stderr).strip().splitlines()
        raise ModelError(f"{path.name} could not be read as a STEP file." + (f" {detail[-1].strip()}" if detail else ""))
    return out


def load_objects(path: Path) -> dict:
    """The model as objects and parts, in millimetres, oriented and placed as the file has them.

    Same shape as _load_3mf returns. STL and OBJ files are one object with one part.
    """
    if not path.is_file():
        raise ModelError(f"Model not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".3mf":
        loaded = _load_3mf(path)
        for obj in loaded["objects"]:
            obj["name"] = obj["name"] or clean_name(path.stem)
            for i, part in enumerate(obj["parts"]):
                part["name"] = part["name"] or (obj["name"] if len(obj["parts"]) == 1 else f"{obj['name']} {i + 1}")
        return loaded
    if suffix == ".stl":
        tris = _load_stl(path)
    elif suffix in CAD_SUFFIXES:
        with tempfile.TemporaryDirectory(prefix="creator5_step_") as folder:
            tris = _load_obj(convert_step(path, Path(folder)))
    elif suffix == ".obj":
        tris = _load_obj(path)
    else:
        raise ModelError(f"{path.suffix} files cannot be read here. Use STL, OBJ, 3MF or STEP.")
    name = clean_name(path.stem)
    return {"objects": [{"name": name, "parts": [{"name": name, "tris": tris, "extruder": None, "paint": None}]}],
            "colours": [], "filaments": 0, "notes": []}


def filaments_needed(loaded: dict) -> int:
    """How many toolheads the model uses: its highest assigned toolhead, or, when it is
    colour-painted, the number of filaments its file was saved with."""
    parts = [part for obj in loaded["objects"] for part in obj["parts"]]
    needed = max([part["extruder"] or 1 for part in parts], default=1)
    if any(part["paint"] for part in parts):
        needed = max(needed, loaded["filaments"])
    return needed


def load_mesh(path: Path) -> tuple[np.ndarray, list[str]]:
    """Every printed triangle (n, 3, 3) in millimetres, plus any notes about the file."""
    loaded = load_objects(path)
    return np.concatenate([part["tris"] for obj in loaded["objects"] for part in obj["parts"]]), loaded["notes"]


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
                "note": "Shape analysis covers STL, OBJ, 3MF and STEP."}
    loaded = load_objects(path)
    tris, notes = np.concatenate([part["tris"] for obj in loaded["objects"] for part in obj["parts"]]), loaded["notes"]
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
        "objects": [obj["name"] for obj in loaded["objects"]],
        "parts": sum(len(obj["parts"]) for obj in loaded["objects"]),
        "filaments_needed": filaments_needed(loaded),
        "colours": loaded["colours"],
        "notes": notes,
    }
