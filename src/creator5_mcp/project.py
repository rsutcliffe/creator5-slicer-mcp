"""Write a slicer project (.3mf) holding the model and every chosen setting.

Opening the project in Flash Studio or OrcaSlicer shows the model on the plate
with the Creator 5 presets selected and the recommended values applied on top,
so Slice and Print work as usual through the slicer's own printer connection.

The layout follows what Flash Studio (Orca-Flashforge) reads on import:
  3D/3dmodel.model                 geometry and plate placement
  Metadata/project_settings.config merged printer, process and filament settings
The settings file names the system presets and lists, in
`different_settings_to_system`, exactly which keys differ from them. On load
the slicer selects those presets and applies only the listed keys.
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import numpy as np

from . import model as model_mod
from . import profiles

APPLICATION = "BambuStudio-01.10.01.50"   # the generator tag Orca-based slicers write and accept
BED = 256.0
GAP = 8.0

_NOT_SETTINGS = {"type", "name", "from", "inherits", "setting_id", "instantiation", "filament_id", "renamed_from",
                 "is_custom_defined", "version", "compatible_printers", "compatible_prints",
                 "compatible_printers_condition", "compatible_prints_condition", "description", "base_id", "include"}

_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                  ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                  ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
                  ' <Default Extension="png" ContentType="image/png"/>\n'
                  ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
                  '</Types>\n')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
         ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
         '</Relationships>\n')


class ProjectError(RuntimeError):
    pass


FILAMENT_DIAMETER = 1.75  # mm, the only size the Creator 5 takes

COLOURS = {"black": "#161616", "white": "#F2F2F2", "grey": "#8C8C8C", "gray": "#8C8C8C", "silver": "#C0C0C0",
           "red": "#C8102E", "orange": "#F26722", "yellow": "#FFD100", "green": "#2E8B57", "blue": "#1F5AA6",
           "purple": "#6A3D9A", "pink": "#F4A6C6", "brown": "#7B4A2A", "gold": "#D4AF37", "beige": "#E8D8B0",
           "natural": "#EDE6D6", "clear": "#E6F2F7", "transparent": "#E6F2F7"}


def colour_for(filament: str, default: str = "#161616") -> str:
    """Display colour for the filament, from a colour word in how the user named it."""
    words = filament.lower().replace("-", " ").split()
    return next((COLOURS[w] for w in words if w in COLOURS), default)


def project_settings(profiles_dir: Path, plan: dict, extra_process: dict | None = None,
                     extra_filament: dict | None = None, colour: str = "#161616") -> tuple[dict, list[str]]:
    """Merge the three presets and the overrides into one project settings document.

    Returns the document and the caller's override keys the presets do not define
    (passed through, but worth flagging: a misspelt key is silently ignored by the slicer).
    """
    machine = profiles.flatten(profiles_dir, "machine", plan["machine_preset"])
    process_over = {**plan["process_overrides"], **(extra_process or {})}
    filament_over = {**plan["filament_overrides"], **(extra_filament or {})}
    process = profiles.flatten(profiles_dir, "process", plan["process_preset"])
    filament = profiles.flatten(profiles_dir, "filament", plan["filament_preset"])
    unknown = [k for k in (extra_process or {}) if k not in process] + [k for k in (extra_filament or {}) if k not in filament]
    process, _ = profiles.apply_overrides(process, process_over)
    filament, _ = profiles.apply_overrides(filament, filament_over)

    # A different diameter means a preset was resolved wrongly, and printing it would badly
    # under- or over-extrude.
    diameters = filament.get("filament_diameter")
    diameters = diameters if isinstance(diameters, list) else [diameters]
    if any(d is None or abs(float(d) - FILAMENT_DIAMETER) > 0.01 for d in diameters):
        raise ProjectError(f"Refusing to build the project: the filament preset resolves to a diameter of {diameters} mm, "
                           f"but the Creator 5 uses {FILAMENT_DIAMETER} mm. Please report this.")

    merged: dict = {}
    for preset in (machine, process, filament):
        merged.update({k: v for k, v in preset.items() if k not in _NOT_SETTINGS})
    merged.update({
        "name": "project_settings",
        "from": "project",
        "version": APPLICATION.split("-", 1)[1],
        "printer_settings_id": plan["machine_preset"],
        "print_settings_id": plan["process_preset"],
        "filament_settings_id": [plan["filament_preset"]],
        "filament_colour": [colour],
        # One entry each for: process, filament, printer.
        "inherits_group": ["", "", ""],
        "different_settings_to_system": [";".join(sorted(process_over)), ";".join(sorted(filament_over)), ""],
    })
    return merged, unknown


def _mesh(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        tris, _ = model_mod.load_mesh(path)   # STL, OBJ or 3MF, oriented as the file has it
    except model_mod.ModelError as exc:
        raise ProjectError(str(exc)) from exc
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    # Centre on the XY origin with the base at z = 0, so placement is a plain translation.
    tris = tris - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    vertices, index = np.unique(np.round(tris.reshape(-1, 3), 5), axis=0, return_inverse=True)
    faces = index.reshape(-1, 3)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    return vertices, faces, hi - lo


def _positions(size: np.ndarray, copies: int) -> list[tuple[float, float]]:
    """Centres for `copies` parts in a grid in the middle of the bed."""
    cols = min(copies, max(1, int((BED - GAP) // (size[0] + GAP))))
    rows = math.ceil(copies / cols)
    if rows * (size[1] + GAP) > BED or size[0] > BED:
        raise ProjectError(f"{copies} copies of a {size[0]:.0f} x {size[1]:.0f} mm part do not fit on the {BED:.0f} mm bed.")
    total_w = cols * size[0] + (cols - 1) * GAP
    total_d = rows * size[1] + (rows - 1) * GAP
    out = []
    for i in range(copies):
        row, col = divmod(i, cols)
        out.append((BED / 2 - total_w / 2 + size[0] / 2 + col * (size[0] + GAP),
                    BED / 2 - total_d / 2 + size[1] / 2 + row * (size[1] + GAP)))
    return out


def _model_xml(name: str, vertices: np.ndarray, faces: np.ndarray, positions: list[tuple[float, float]]) -> str:
    safe = name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n',
             f' <metadata name="Application">{APPLICATION}</metadata>\n',
             ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n',
             f' <metadata name="Title">{safe}</metadata>\n',
             ' <resources>\n', f'  <object id="1" type="model" name="{safe}">\n   <mesh>\n    <vertices>\n']
    parts += [f'     <vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>\n' for x, y, z in vertices]
    parts.append('    </vertices>\n    <triangles>\n')
    parts += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in faces]
    parts.append('    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build>\n')
    parts += [f'  <item objectid="1" transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} 0" printable="1"/>\n' for x, y in positions]
    parts.append(' </build>\n</model>\n')
    return "".join(parts)


def write_project(profiles_dir: Path, model_path: Path, plan: dict, out_path: Path, copies: int = 1,
                  extra_process: dict | None = None, extra_filament: dict | None = None,
                  colour: str = "#161616") -> dict:
    """Create the .3mf project and return a short description of it."""
    if copies < 1:
        raise ProjectError("copies must be at least 1")
    vertices, faces, size = _mesh(model_path)
    positions = _positions(size, copies)
    settings, unknown = project_settings(profiles_dir, plan, extra_process, extra_filament, colour)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("3D/3dmodel.model", _model_xml(model_path.stem, vertices, faces, positions))
        archive.writestr("Metadata/project_settings.config", json.dumps(settings, indent=4))
    return {
        "project": str(out_path),
        "copies": copies,
        "part_size_mm": [round(float(s), 1) for s in size],
        "changed_process_settings": settings["different_settings_to_system"][0].split(";"),
        "changed_filament_settings": [k for k in settings["different_settings_to_system"][1].split(";") if k],
        "unknown_override_keys": unknown,
        "settings": settings,
    }
