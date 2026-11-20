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


# Process settings Flash Studio accepts on an object (PrintObjectConfig and PrintRegionConfig in
# FlashForge/Orca-Flashforge, src/libslic3r/PrintConfig.hpp). Changed ones are written on every object as
# well as on the process preset: about a second after a new window loads a project, Flash Studio signs in,
# loads the user's saved presets and, when one of them sorts ahead of the chosen process preset, selects
# that preset again, which discards every change made to it (seen 20 Nov 2026: supports, walls and infill
# all back to stock). Settings on an object are part of the model and survive.
OBJECT_SETTINGS = frozenset("""
adaptive_layer_height alternate_extra_wall bottom_shell_layers bottom_shell_thickness
bottom_solid_infill_flow_ratio bottom_surface_pattern bridge_acceleration bridge_angle bridge_density
bridge_flow bridge_no_support bridge_speed brim_ears_detection_length brim_ears_max_angle brim_object_gap
brim_type brim_width counterbore_hole_bridging default_acceleration default_jerk
detect_narrow_internal_solid_infill detect_overhang_wall detect_thin_wall dont_filter_internal_bridges
elefant_foot_compensation elefant_foot_compensation_layers enable_extra_bridge_layer enable_overhang_speed
enable_support enforce_support_layers ensure_vertical_shell_thickness extra_perimeters_on_overhangs
filter_out_gap_fill flush_into_infill flush_into_objects flush_into_support fuzzy_skin fuzzy_skin_first_layer
fuzzy_skin_noise_type fuzzy_skin_octaves fuzzy_skin_persistence fuzzy_skin_point_distance fuzzy_skin_scale
fuzzy_skin_thickness gap_fill_target gap_infill_speed hole_to_polyhole hole_to_polyhole_threshold
hole_to_polyhole_twisted independent_support_layer_height infill_anchor infill_anchor_max infill_combination
infill_combination_max_layer_height infill_direction infill_jerk infill_wall_overlap initial_layer_acceleration
initial_layer_jerk initial_layer_min_bead_width inner_wall_acceleration inner_wall_jerk inner_wall_line_width
inner_wall_speed interface_shells interlocking_beam interlocking_beam_layer_count interlocking_beam_width
interlocking_boundary_avoidance interlocking_depth interlocking_orientation internal_bridge_angle
internal_bridge_density internal_bridge_flow internal_bridge_speed internal_solid_infill_acceleration
internal_solid_infill_line_width internal_solid_infill_pattern internal_solid_infill_speed ironing_angle
ironing_direction ironing_flow ironing_inset ironing_pattern ironing_spacing ironing_speed ironing_type
is_infill_first lattice_angle_1 lattice_angle_2 layer_height line_width make_overhang_printable
make_overhang_printable_angle make_overhang_printable_hole_size max_bridge_length min_bead_width
min_feature_size min_length_factor min_width_top_surface minimum_sparse_infill_area
mmu_segmented_region_interlocking_depth mmu_segmented_region_max_width only_one_wall_first_layer
only_one_wall_top outer_wall_acceleration outer_wall_jerk outer_wall_line_width outer_wall_speed
overhang_1_4_speed overhang_2_4_speed overhang_3_4_speed overhang_4_4_speed overhang_reverse
overhang_reverse_internal_only overhang_reverse_threshold overhang_speed_classic precise_outer_wall
precise_z_height print_flow_ratio raft_contact_distance raft_expansion raft_first_layer_density
raft_first_layer_expansion raft_layers role_based_wipe_speed rotate_solid_infill_direction scarf_angle_threshold
scarf_joint_flow_ratio scarf_joint_speed scarf_overhang_threshold seam_gap seam_position seam_slope_conditional
seam_slope_entire_loop seam_slope_inner_walls seam_slope_min_length seam_slope_start_height seam_slope_steps
seam_slope_type skirt_start_angle slice_closing_radius slicing_mode slowdown_for_curled_perimeters
small_area_infill_flow_compensation small_perimeter_speed small_perimeter_threshold solid_infill_direction
solid_infill_filament sparse_infill_acceleration sparse_infill_density sparse_infill_filament
sparse_infill_line_width sparse_infill_pattern sparse_infill_speed staggered_inner_seams support_angle
support_base_pattern support_base_pattern_spacing support_bottom_interface_spacing support_bottom_z_distance
support_critical_regions_only support_expansion support_filament support_interface_bottom_layers
support_interface_filament support_interface_loop_pattern support_interface_not_for_body
support_interface_pattern support_interface_spacing support_interface_speed support_interface_top_layers
support_line_width support_object_first_layer_gap support_object_xy_distance support_on_build_plate_only
support_remove_small_overhang support_speed support_style support_threshold_angle support_threshold_overlap
support_top_z_distance support_type thick_bridges thick_internal_bridges top_bottom_infill_wall_overlap
top_shell_layers top_shell_thickness top_solid_infill_flow_ratio top_surface_acceleration top_surface_jerk
top_surface_line_width top_surface_pattern top_surface_speed travel_acceleration travel_jerk
tree_support_adaptive_layer_height tree_support_angle_slow tree_support_auto_brim tree_support_branch_angle
tree_support_branch_angle_organic tree_support_branch_diameter tree_support_branch_diameter_angle
tree_support_branch_diameter_organic tree_support_branch_distance tree_support_branch_distance_organic
tree_support_brim_width tree_support_tip_diameter tree_support_top_rate tree_support_wall_count wall_direction
wall_distribution_count wall_filament wall_generator wall_infill_order wall_loops wall_sequence
wall_transition_angle wall_transition_filter_deviation wall_transition_length wipe_before_external_loop
wipe_on_loops wipe_speed xy_contour_compensation xy_hole_compensation
""".split())
# Changed process settings that only exist on the preset, and that the reset would not alter in practice.
_RESET_HARMLESS = {"enable_prime_tower"}   # the preset turns it on; with one filament no tower is printed anyway

class ProjectError(RuntimeError):
    pass


FILAMENT_DIAMETER = 1.75  # mm, the only size the Creator 5 takes

COLOURS = {"black": "#161616", "white": "#F2F2F2", "grey": "#8C8C8C", "gray": "#8C8C8C", "silver": "#C0C0C0",
           "red": "#C8102E", "orange": "#F26722", "yellow": "#FFD100", "green": "#2E8B57", "blue": "#1F5AA6",
           "purple": "#6A3D9A", "pink": "#F4A6C6", "brown": "#7B4A2A", "gold": "#D4AF37", "beige": "#E8D8B0",
           "natural": "#EDE6D6", "clear": "#E6F2F7", "transparent": "#E6F2F7"}


def colour_for(filament: str, default: str = "#161616") -> str:
    """Display colour for the filament, from a colour word in how the user named it."""
    words = filament.lower().replace("-", " ").replace(",", " ").split()
    for word in words:
        if word in COLOURS:
            return COLOURS[word]
        if len(word) == 7 and word[0] == "#" and all(c in "0123456789abcdef" for c in word[1:]):
            return word.upper()
    return default


def project_settings(profiles_dir: Path, plan: dict, extra_process: dict | None = None,
                     extra_filament: dict | None = None, colour: str = "#161616") -> tuple[dict, list[str]]:
    """Merge the presets and the overrides into one project settings document.

    plan["filaments"], when present, lists one {"preset", "overrides", "colour"} per toolhead;
    otherwise the plan's single filament is used with `colour`. extra_filament applies to all.
    Returns the document and the caller's override keys the presets do not define
    (passed through, but worth flagging: a misspelt key is silently ignored by the slicer).

    Only toolhead 1's filament carries changed values. Flash Studio replaces any other filament
    that is not one of its stock presets with a generic one when it slices (seen three times on
    the machine, 16 and 17 Nov 2026: inline changes, a preset embedded in the project and a preset
    saved in Flash Studio all became Generic HS PETG). Toolheads 2 to 4 are written as stock presets.
    The printer's bed_temperature_formula was tried too: by_first_filament follows whichever toolhead
    prints first on each layer, so it does not hold the bed at toolhead 1's temperature.
    """
    slots = plan.get("filaments") or [{"preset": plan["filament_preset"], "overrides": plan["filament_overrides"], "colour": colour}]
    machine = profiles.flatten(profiles_dir, "machine", plan["machine_preset"])
    process_over = {**plan["process_overrides"], **(extra_process or {})}
    process = profiles.flatten(profiles_dir, "process", plan["process_preset"])
    unknown = [k for k in (extra_process or {}) if k not in process]
    process, _ = profiles.apply_overrides(process, process_over)

    filaments, changed = [], []
    for index, slot in enumerate(slots):
        over = {} if index else {**slot["overrides"], **(extra_filament or {})}
        flat = profiles.flatten(profiles_dir, "filament", slot["preset"])
        unknown += [k for k in (extra_filament or {}) if k not in flat and k not in unknown]
        flat, _ = profiles.apply_overrides(flat, over)
        # A different diameter means a preset was resolved wrongly, and printing it would badly
        # under- or over-extrude.
        diameters = flat.get("filament_diameter")
        diameters = diameters if isinstance(diameters, list) else [diameters]
        if any(d is None or abs(float(d) - FILAMENT_DIAMETER) > 0.01 for d in diameters):
            raise ProjectError(f"Refusing to build the project: the filament preset resolves to a diameter of {diameters} mm, "
                               f"but the Creator 5 uses {FILAMENT_DIAMETER} mm. Please report this.")
        filaments.append(flat)
        changed.append(";".join(sorted(over)))

    merged: dict = {}
    for preset in (machine, process):
        merged.update({k: v for k, v in preset.items() if k not in _NOT_SETTINGS})
    # Filament settings hold one value per toolhead.
    for key in {k for flat in filaments for k in flat if k not in _NOT_SETTINGS}:
        # A preset that leaves a key out borrows the value of one that has it: a gap in the list
        # makes the slicer reject the whole settings file and fall back to its defaults.
        known = next(flat[key] for flat in filaments if flat.get(key) is not None)
        values = [flat.get(key) if flat.get(key) is not None else known for flat in filaments]
        merged[key] = [v[0] if isinstance(v, list) and v else v for v in values] if any(isinstance(v, list) for v in values) else values[0]
    merged.update({
        "name": "project_settings",
        "from": "project",
        "version": APPLICATION.split("-", 1)[1],
        "printer_settings_id": plan["machine_preset"],
        "print_settings_id": plan["process_preset"],
        "filament_settings_id": [slot["preset"] for slot in slots],
        "filament_colour": [slot["colour"] for slot in slots],
        # Which toolhead each entry of a filament setting belongs to. Without it the slicer reads
        # toolhead 1's values for every toolhead (seen in the G-code; Flash Studio writes it on save).
        "filament_self_index": [str(i + 1) for i in range(len(slots))],
        # One entry each for: process, every filament, printer.
        "inherits_group": [""] * (len(slots) + 2),
        "different_settings_to_system": [";".join(sorted(process_over)), *changed, ""],
    })
    return merged, unknown


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _layout(objects: list[dict], copies: int) -> tuple[np.ndarray, np.ndarray, list[tuple[float, float]]]:
    """Offset that centres the whole model on the XY origin with its base at z = 0, its size,
    and where each copy's centre goes on the bed. Objects keep their positions relative to each other."""
    points = np.concatenate([part["tris"].reshape(-1, 3) for obj in objects for part in obj["parts"]])
    lo, hi = points.min(axis=0), points.max(axis=0)
    size = hi - lo
    cols = min(copies, max(1, int((BED - GAP) // (size[0] + GAP))))
    rows = math.ceil(copies / cols)
    if rows * (size[1] + GAP) - GAP > BED or size[0] > BED:
        raise ProjectError(f"{copies} x a {size[0]:.0f} x {size[1]:.0f} mm model does not fit on the {BED:.0f} mm bed.")
    total_w = cols * size[0] + (cols - 1) * GAP
    total_d = rows * size[1] + (rows - 1) * GAP
    positions = []
    for i in range(copies):
        row, col = divmod(i, cols)
        positions.append((BED / 2 - total_w / 2 + size[0] / 2 + col * (size[0] + GAP),
                          BED / 2 - total_d / 2 + size[1] / 2 + row * (size[1] + GAP)))
    return np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]), size, positions


def _documents(title: str, objects: list[dict], copies: int, toolheads: int,
               object_settings: dict | None = None) -> tuple[str, str, np.ndarray]:
    """3D/3dmodel.model and Metadata/model_settings.config, as Flash Studio reads them.

    Each object is a parent whose components are its parts; model_settings.config gives every
    object and part its name and toolhead (_BBS_3MF_Importer matches a <part id> to the
    component's object id). object_settings are written on every object, as the slicer's own
    per-object settings are.
    """
    offset, size, positions = _layout(objects, copies)
    per_object = "".join(f'    <metadata key="{_xml(k)}" value="{_xml(str(v))}"/>\n' for k, v in sorted((object_settings or {}).items()))
    model = ['<?xml version="1.0" encoding="UTF-8"?>\n',
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n',
             f' <metadata name="Application">{APPLICATION}</metadata>\n',
             ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n',
             f' <metadata name="Title">{_xml(title)}</metadata>\n',
             ' <resources>\n']
    config = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n']
    build = []
    next_id = 1
    for obj in objects:
        part_ids = []
        part_config = []
        for part in obj["parts"]:
            tris = part["tris"] - offset
            vertices, index = np.unique(np.round(tris.reshape(-1, 3), 5), axis=0, return_inverse=True)
            faces = index.reshape(-1, 3)
            paint = part["paint"]
            if paint is None:   # painted triangles must keep their order, so only unpainted parts drop slivers
                faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
            model.append(f'  <object id="{next_id}" type="model" name="{_xml(part["name"])}">\n   <mesh>\n    <vertices>\n')
            model += [f'     <vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>\n' for x, y, z in vertices]
            model.append('    </vertices>\n    <triangles>\n')
            if paint is None:
                model += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in faces]
            else:
                model += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"' + (f' paint_color="{_xml(colour)}"' if colour else "") + '/>\n'
                          for (a, b, c), colour in zip(faces, paint)]
            model.append('    </triangles>\n   </mesh>\n  </object>\n')
            extruder = min(part["extruder"] or 1, toolheads)
            part_config.append(f'    <part id="{next_id}" subtype="normal_part">\n'
                               f'      <metadata key="name" value="{_xml(part["name"])}"/>\n'
                               f'      <metadata key="extruder" value="{extruder}"/>\n    </part>\n')
            part_ids.append(next_id)
            next_id += 1
        model.append(f'  <object id="{next_id}" type="model" name="{_xml(obj["name"])}">\n   <components>\n')
        model += [f'    <component objectid="{pid}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n' for pid in part_ids]
        model.append('   </components>\n  </object>\n')
        first = min(obj["parts"][0]["extruder"] or 1, toolheads)
        config.append(f'  <object id="{next_id}">\n    <metadata key="name" value="{_xml(obj["name"])}"/>\n'
                      f'    <metadata key="extruder" value="{first}"/>\n' + per_object + "".join(part_config) + '  </object>\n')
        build += [f'  <item objectid="{next_id}" transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} 0" printable="1"/>\n' for x, y in positions]
        next_id += 1
    model.append(' </resources>\n <build>\n')
    model += build
    model.append(' </build>\n</model>\n')
    config.append('</config>\n')
    return "".join(model), "".join(config), size


def write_project(profiles_dir: Path, model_path: Path, plan: dict, out_path: Path, copies: int = 1,
                  extra_process: dict | None = None, extra_filament: dict | None = None,
                  colour: str = "#161616", pause_before_z: float = 0) -> dict:
    """Create the .3mf project and return a short description of it.

    colour is used only when the plan does not list its filaments.
    pause_before_z, in mm, pauses the print before the layer whose top is at that height,
    with the printer's own pause command."""
    if copies < 1:
        raise ProjectError("copies must be at least 1")
    try:
        loaded = model_mod.load_objects(model_path)   # STL, OBJ or 3MF, oriented and placed as the file has it
    except model_mod.ModelError as exc:
        raise ProjectError(str(exc)) from exc
    settings, unknown = project_settings(profiles_dir, plan, extra_process, extra_filament, colour)
    title = loaded["objects"][0]["name"]
    changed_process = [k for k in settings["different_settings_to_system"][0].split(";") if k]
    on_objects = {k: settings[k] for k in changed_process if k in OBJECT_SETTINGS and isinstance(settings[k], str)}
    model_xml, config_xml, size = _documents(title, loaded["objects"], copies, len(settings["filament_settings_id"]), on_objects)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("3D/3dmodel.model", model_xml)
        archive.writestr("Metadata/model_settings.config", config_xml)
        archive.writestr("Metadata/project_settings.config", json.dumps(settings, indent=4))
        if pause_before_z:
            # As _BBS_3MF_Exporter writes it: type 1 is a pause, at the print height of the layer it comes before.
            mode = "MultiExtruder" if len(settings["filament_settings_id"]) > 1 else "SingleExtruder"
            archive.writestr("Metadata/custom_gcode_per_layer.xml",
                             '<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n<plate>\n<plate_info id="1"/>\n'
                             f'<layer top_z="{pause_before_z:g}" type="1" extruder="1" color="" extra="" '
                             f'gcode="{_xml(str(settings.get("machine_pause_gcode") or ""))}"/>\n'
                             f'<mode value="{mode}"/>\n</plate>\n</custom_gcodes_per_layer>\n')
    return {
        "project": str(out_path),
        "copies": copies,
        "objects": [obj["name"] for obj in loaded["objects"]],
        "part_size_mm": [round(float(v), 1) for v in size],
        "changed_process_settings": settings["different_settings_to_system"][0].split(";"),
        "changed_filament_settings": [k for k in settings["different_settings_to_system"][1].split(";") if k],
        "unknown_override_keys": unknown,
        "preset_only_process_settings": [k for k in changed_process if k not in on_objects and k not in _RESET_HARMLESS],
        "settings": settings,
    }
