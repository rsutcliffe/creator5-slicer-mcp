"""MCP tools: analyse a model, recommend settings, slice, and open the result in the slicer."""

from __future__ import annotations

import platform
import re
import subprocess
import time
from pathlib import Path

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from . import __version__, model, profiles, project, recommend, results, slicer, spools
from .config import ConfigError, load_settings

mcp = FastMCP(
    "creator5-slicer",
    instructions=(
        "Slices 3D models for a Flashforge Creator 5 using Flash Studio. Typical flow: call "
        "slice_model with the model path, the filament the user named, and a quality and strength "
        "level that suit the part's purpose. It analyses the shape, picks presets and settings, "
        "writes a project file, slices that project to check it, and returns time and filament estimates. "
        "Then, without asking, call open_in_slicer with the project_3mf path so the user can press Slice "
        "and Print in Flash Studio. Pass auto_orient only when the user asks for it. Relay the returned "
        "warnings to the user. If the user gives figures from the spool's label, pass them as "
        "nozzle_temp, bed_temp and max_speed, and offer to keep them with save_spool so the spool's name is "
        "enough next time. For a multi-colour model, pass the filaments in toolheads 2 to 4 as other_filaments. "
        "Use recommend_settings first when the user wants to review choices. When the user says how a print "
        "came out, call record_print_result. The user prints from Flash Studio; "
        "this server never sends anything to the printer."
    ),
)

_ERRORS = (ConfigError, profiles.ProfileError, model.ModelError, recommend.RecommendError,
           slicer.SliceError, project.ProjectError, results.ResultError)


def _fail(exc: Exception) -> dict:
    return {"ok": False, "error": str(exc)}


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@mcp.tool()
def check_setup() -> dict:
    """Report whether the slicer and its Creator 5 presets are ready."""
    settings = load_settings()
    report: dict = {
        "ok": settings.slicer_path is not None and settings.profiles_dir is not None,
        "server_version": __version__,
        "slicer_path": str(settings.slicer_path) if settings.slicer_path else None,
        "profiles_dir": str(settings.profiles_dir) if settings.profiles_dir else None,
        "output_dir": str(settings.output_dir),
    }
    if settings.slicer_path:
        report["slicer_version"] = slicer.version(settings)
    else:
        report["fix"] = "Install OrcaSlicer or Flash Studio, or set ORCA_SLICER_PATH."
    if settings.slicer_path and not settings.profiles_dir:
        try:
            settings.require_profiles()
        except ConfigError as exc:
            report["fix"] = str(exc)
    if settings.profiles_dir:
        try:
            report["nozzles"] = {n: list(profiles.process_presets(settings.profiles_dir, n).values())
                                 for n in profiles.NOZZLES}
        except profiles.ProfileError as exc:
            report["ok"], report["error"] = False, str(exc)
    return report


@mcp.tool()
def list_filaments(nozzle: str = "0.4", pro: bool = False) -> dict:
    """List the filament types this server understands, the Creator 5 presets installed, and the saved spools.

    Args:
        nozzle: Nozzle diameter in mm: 0.25, 0.4, 0.6 or 0.8.
        pro: True for the Creator 5 Pro.
    """
    try:
        settings = load_settings()
        return {
            "ok": True,
            "spools": spools.load(settings.output_dir),
            "understood_types": [m.label for m in recommend.MATERIALS],
            "installed_presets": profiles.filament_presets(settings.require_profiles(), nozzle, pro),
            "quality_levels": {k: v[1] for k, v in recommend.QUALITY.items()},
            "strength_levels": {k: v.why for k, v in recommend.STRENGTH.items()},
        }
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def save_spool(name: str, filament: str, nozzle_temp: int = 0, bed_temp: int = 0, max_speed: int = 0,
               colour: str = "", max_flow: float = 0) -> dict:
    """Remember a spool and its label figures, so its name alone is enough in later requests.

    Args:
        name: What the user calls the spool, e.g. "black pla" or "the silk gold".
        filament: The material and maker, e.g. "Creality Ender PLA", "PETG", "silk PLA".
        nozzle_temp: Nozzle temperature in C from the label, or 0 to use the preset.
        bed_temp: Bed temperature in C from the label, or 0 to use the preset.
        max_speed: Fastest print speed in mm/s on the label, or 0 for no extra limit.
        colour: A colour word such as "black", or a hex value such as "#161616".
        max_flow: Flow limit in mm3/s proven by a good print or a flow test, or 0. When set it is used at
            every layer height, in place of the cap worked out from max_speed.
    """
    try:
        settings = load_settings()
        recommend.match_material(filament)   # refuse a material this server cannot place
        return {"ok": True, "spool": spools.save(settings.output_dir, name, filament, nozzle_temp, bed_temp, max_speed, colour, max_flow)}
    except _ERRORS + (ValueError,) as exc:
        return _fail(exc)


@mcp.tool()
def record_print_result(project_3mf: str, outcome: str, notes: str = "", printed_project: str | None = None) -> dict:
    """Record how a print came out, so settings can be corrected from real prints.

    Call this when the user says how a print went. The record keeps the project's presets and
    changed settings with the outcome, in print_results.json in the output folder.

    Args:
        project_3mf: The project_3mf path slice_model returned for the job that was printed.
        outcome: good, acceptable or failed.
        notes: What the user said: first layer, walls, support removal, strength, fit, anything changed by hand.
        printed_project: Optional path to the project as the user saved it from Flash Studio after editing it.
            What differs from project_3mf is recorded as changed by hand.
    """
    try:
        settings = load_settings()
        entry = results.record(settings.output_dir, _path(project_3mf), outcome, notes,
                               _path(printed_project) if printed_project else None)
        return {"ok": True, "result": entry, "results_recorded": len(results.load(settings.output_dir))}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def compare_with_saved_project(project_3mf: str, saved_project: str) -> dict:
    """Show what the user changed by hand: the project this server wrote against the one saved from Flash Studio.

    Args:
        project_3mf: The project_3mf path slice_model returned.
        saved_project: The project the user saved from Flash Studio after editing it.
    """
    try:
        return {"ok": True, "changed_by_hand": results.differences(_path(project_3mf), _path(saved_project))}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def analyse_model(model_path: str) -> dict:
    """Measure an STL, OBJ or 3MF: size, volume, bed contact, overhangs, parts, toolheads used and bed fit.

    Args:
        model_path: Absolute path to the model file on this computer.
    """
    try:
        return {"ok": True, **model.analyse(_path(model_path))}
    except _ERRORS as exc:
        return _fail(exc)


_FALLBACK_COLOURS = ("#161616", "#F2F2F2", "#C8102E", "#1F5AA6")   # toolheads 1 to 4, when nothing says otherwise


def _spool(settings, text: str, nozzle_temp: int = 0, bed_temp: int = 0, max_speed: int = 0) -> dict:
    """What the user said about one filament, with any saved spool's figures filled in.
    Figures passed with the request beat the register."""
    saved = spools.find(settings.output_dir, text)
    said = project.colour_for(text, "")
    if not saved:
        return {"filament": text, "nozzle_temp": nozzle_temp, "bed_temp": bed_temp, "max_speed": max_speed, "max_flow": 0,
                "colour": said, "note": None}
    return {"filament": saved["filament"], "nozzle_temp": nozzle_temp or saved["nozzle_temp"],
            "bed_temp": bed_temp or saved["bed_temp"], "max_speed": max_speed or saved["max_speed"],
            # A speed given with the request is the newer instruction, so it beats the saved flow limit.
            "max_flow": 0 if max_speed else saved.get("max_flow", 0),
            "colour": said or project.colour_for(saved["colour"], ""),
            "note": f"Spool '{saved['name']}' from the register: {saved['filament']}."}


def _plan(model_path: str | None, filament: str, quality: str, strength: str, nozzle: str, supports: str,
          brim: str, layer_height: float | None, precise_fit: bool, smooth_top: bool, pro: bool,
          nozzle_temp: int = 0, bed_temp: int = 0, max_speed: int = 0,
          shape_path: Path | None = None, other_filaments: list[str] | None = None) -> tuple[dict, dict | None]:
    """shape_path: the file whose shape will print, when it differs from model_path (an oriented or converted copy).

    The plan's "filaments" lists one {"preset", "overrides", "colour", "material"} per toolhead used.
    """
    settings = load_settings()
    profiles_dir = settings.require_profiles()
    analysis = designer = None
    if model_path:
        path = _path(model_path)
        designer = model.designer_settings(path)
        angle = float(designer.get("support_threshold_angle") or 0) or model.DEFAULT_SUPPORT_ANGLE
        analysis = model.analyse(shape_path or path, angle)
    shape = analysis if analysis and analysis.get("analysed") else {}
    said = [filament, *(other_filaments or [])]
    limit = len(profiles.flatten(profiles_dir, "machine", profiles.machine_name(nozzle, pro)).get("nozzle_diameter") or [1])
    toolheads = max(len(said), shape.get("filaments_needed", 1))
    if toolheads > limit:
        raise recommend.RecommendError(f"The job needs {toolheads} filaments, but the printer has {limit} toolheads.")

    first = _spool(settings, filament, nozzle_temp, bed_temp, max_speed)
    plan = recommend.recommend(
        profiles_dir, first["filament"], quality, strength, nozzle, analysis, supports, brim,
        layer_height or None, precise_fit, smooth_top, pro, first["nozzle_temp"] or None, first["bed_temp"] or None,
        first["max_speed"] or None, designer, toolheads, first["max_flow"] or None)
    file_colours = shape.get("colours", []) if toolheads > 1 else []
    slots, unnamed = [], []
    for i in range(toolheads):
        spool = first if i == 0 else _spool(settings, said[i]) if i < len(said) else {**first, "colour": "", "note": None}
        if i == 0:
            preset, overrides, material = plan["filament_preset"], plan["filament_overrides"], plan["material"]
        else:
            # Same layer height and line width as the main plan, so flow caps agree.
            sub = recommend.recommend(profiles_dir, spool["filament"], quality, strength, nozzle, None,
                                      layer_height=plan["layer_height_mm"], pro=pro, nozzle_temp=spool["nozzle_temp"] or None,
                                      bed_temp=spool["bed_temp"] or None, max_speed=spool["max_speed"] or None,
                                      max_flow=spool["max_flow"] or None)
            preset, overrides, material = sub["filament_preset"], sub["filament_overrides"], sub["material"]
            plan["warnings"] += [w for w in sub["warnings"] if w not in plan["warnings"]]
            # The other material's own process needs: the slower speed wins, and nothing already chosen is replaced.
            for key, value in recommend.match_material(spool["filament"]).process.items():
                current = plan["process_overrides"].get(key)
                if key.endswith("_speed") and current is not None:
                    value = f"{min(float(value), float(current)):g}"
                if key.endswith("_speed") or current is None:
                    plan["process_overrides"][key] = value
        colour = spool["colour"] or (file_colours[i] if i < len(file_colours) and file_colours[i] else _FALLBACK_COLOURS[i % 4])
        if spool["note"]:
            plan["rationale"].append(f"Toolhead {i + 1}: {spool['note']}" if toolheads > 1 else spool["note"])
        if i >= len(said):
            unnamed.append(f"toolhead {i + 1} ({colour})")
        slots.append({"preset": preset, "overrides": overrides, "colour": colour, "material": material})
    plan["filaments"] = slots
    if toolheads > 1:
        plan["rationale"].append("Toolheads: " + "; ".join(f"{i + 1} = {slot['material']} {slot['colour']}" for i, slot in enumerate(slots)) + ".")
    if unnamed:
        plan["warnings"].append(f"The model uses {toolheads} toolheads but {len(said)} filament{'s were' if len(said) > 1 else ' was'} named. "
                                f"The rest were given toolhead 1's filament, in the model's colours: {', '.join(unnamed)}. "
                                "Load those colours, or name each filament.")
    return plan, analysis


@mcp.tool()
def recommend_settings(
    filament: str,
    model_path: str | None = None,
    quality: str = "standard",
    strength: str = "standard",
    nozzle: str = "0.4",
    supports: str = "auto",
    brim: str = "auto",
    layer_height: float = 0,
    precise_fit: bool = False,
    smooth_top: bool = False,
    pro: bool = False,
    nozzle_temp: int = 0,
    bed_temp: int = 0,
    max_speed: int = 0,
    other_filaments: list[str] | None = None,
) -> dict:
    """Choose presets and settings for a print without slicing, with the reasoning for each choice.

    Args:
        filament: Filament in toolhead 1 as the user describes it, e.g. "black PLA", "PETG", "silk PLA",
            or the name of a saved spool.
        model_path: Optional absolute path to the STL, OBJ or 3MF, so supports and brim respond to its shape
            and to any settings its designer saved in the 3MF.
        quality: draft, standard, fine or best. Sets layer height and outer wall care.
        strength: decorative, standard, functional or structural. Sets walls, infill and shells.
        nozzle: Nozzle diameter in mm: 0.25, 0.4, 0.6 or 0.8.
        supports: auto, on or off.
        brim: auto, on or off.
        layer_height: Exact layer height in mm, or 0 to let quality decide.
        precise_fit: True for parts that must mate with other parts.
        smooth_top: True to iron flat top surfaces.
        pro: True for the Creator 5 Pro.
        nozzle_temp: Nozzle temperature in C from the spool's label, or 0 to use the preset.
        bed_temp: Bed temperature in C from the spool's label, or 0 to use the preset.
        max_speed: Fastest print speed in mm/s the spool's label recommends, or 0 for no extra limit.
        other_filaments: Filaments in toolheads 2, 3 and 4, in order, for a multi-colour model.
    """
    try:
        plan, analysis = _plan(model_path, filament, quality, strength, nozzle, supports, brim,
                               layer_height, precise_fit, smooth_top, pro, nozzle_temp, bed_temp, max_speed,
                               other_filaments=other_filaments)
        return {"ok": True, "plan": plan, "model": analysis}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def slice_model(
    model_path: str,
    filament: str,
    quality: str = "standard",
    strength: str = "standard",
    nozzle: str = "0.4",
    supports: str = "auto",
    brim: str = "auto",
    layer_height: float = 0,
    precise_fit: bool = False,
    smooth_top: bool = False,
    copies: int = 1,
    auto_orient: bool = False,
    process_overrides: dict | None = None,
    filament_overrides: dict | None = None,
    job_name: str | None = None,
    pro: bool = False,
    nozzle_temp: int = 0,
    bed_temp: int = 0,
    max_speed: int = 0,
    other_filaments: list[str] | None = None,
) -> dict:
    """Slice a model for the Creator 5 with settings chosen for the filament and purpose.

    Writes a project file (project_3mf) to open in the slicer and print from, then slices that same
    project to check it: print time, filament use, and that every chosen setting reached the G-code.
    Returns those, the settings applied with reasons, and any warnings to pass on to the user.
    A 3MF that carries its designer's slicer settings keeps the designer's supports and brim.
    A multi-colour 3MF keeps its objects, parts and toolhead assignments.

    Args:
        model_path: Absolute path to an STL, 3MF or OBJ file on this computer.
        filament: Filament in toolhead 1 as the user describes it, e.g. "black PLA", "PETG", "TPU 95A",
            or the name of a saved spool.
        quality: draft, standard, fine or best.
        strength: decorative, standard, functional or structural.
        nozzle: Nozzle diameter in mm: 0.25, 0.4, 0.6 or 0.8.
        supports: auto, on or off.
        brim: auto, on or off.
        layer_height: Exact layer height in mm, or 0 to let quality decide.
        precise_fit: True for parts that must mate with other parts.
        smooth_top: True to iron flat top surfaces.
        copies: Number of copies to arrange on the plate.
        auto_orient: Let the slicer turn the part to its best orientation, in the project and the G-code alike.
            Leave False when the model is already oriented for strength, as designers' files usually are.
        process_overrides: Extra OrcaSlicer process settings applied last, e.g. {"wall_loops": 5}.
        filament_overrides: Extra OrcaSlicer filament settings for every toolhead, e.g. {"nozzle_temperature": 215}.
        job_name: Name for the output folder and files.
        pro: True for the Creator 5 Pro.
        nozzle_temp: Nozzle temperature in C from the label of the spool in toolhead 1, or 0 to use the preset.
        bed_temp: Bed temperature in C from that label, or 0 to use the preset.
        max_speed: Fastest print speed in mm/s that label recommends, or 0 for no extra limit.
        other_filaments: Filaments in toolheads 2, 3 and 4, in order, for a multi-colour model.
    """
    try:
        settings = load_settings()
        profiles_dir = settings.require_profiles()
        source = _path(model_path)
        if not source.is_file():
            raise model.ModelError(f"Model not found: {source}")
        if copies < 1:
            raise project.ProjectError("copies must be at least 1")
        notes: list[str] = []
        loaded = model.load_objects(source)
        # A downloaded file is often named by a hash; the name its designer gave the part reads better.
        title = loaded["objects"][0]["name"] if source.suffix.lower() == ".3mf" else source.stem
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", job_name or title).strip("_") or "print"
        job_dir = settings.output_dir / f"{stamp}_{name}"
        job_dir.mkdir(parents=True, exist_ok=True)

        # Designer settings come from the original file; the shape from the file that will print.
        shaped = source
        several = len(loaded["objects"]) > 1 or len(loaded["objects"][0]["parts"]) > 1
        if auto_orient and several:
            notes.append("Orientation left as designed: turning a model of several parts would merge them into one.")
        elif auto_orient:
            shaped = slicer.orient(settings, source, job_dir, name)
            notes.append("The slicer chose the orientation; the project and the G-code both use it.")
        plan, analysis = _plan(str(source), filament, quality, strength, nozzle, supports, brim, layer_height,
                               precise_fit, smooth_top, pro, nozzle_temp, bed_temp, max_speed, shape_path=shaped,
                               other_filaments=other_filaments)
        made = project.write_project(profiles_dir, shaped, plan, job_dir / f"{name}.3mf", copies,
                                     process_overrides, filament_overrides)
        result = slicer.slice_project(settings, Path(made["project"]), job_dir)
        result["notes"] = notes + result["notes"]
        chosen = {**plan["process_overrides"], **(process_overrides or {}), **(filament_overrides or {})}
        for slot in plan["filaments"]:
            chosen.update(slot["overrides"])
        expected = {k: made["settings"][k] for k in chosen}
        mismatched = slicer.check_settings(Path(result["gcode"]), expected)
        if mismatched:
            plan["warnings"].append("Some settings did not reach the G-code: " + "; ".join(mismatched))
        if made["unknown_override_keys"]:
            plan["warnings"].append(
                "These override keys are not in the base preset and may be misspelt: "
                + ", ".join(made["unknown_override_keys"]))
        # The setting says what was asked for; the G-code says what will print.
        supports_printed = slicer.support_sections(Path(result["gcode"]))
        supports_on = made["settings"].get("enable_support") == "1"
        if supports_on and not supports_printed:
            result["notes"].append("Supports are on, but the slicer found nothing that needs them, so none will print.")
        elif not supports_on and analysis.get("overhang_area_mm2", 0) > recommend.OVERHANG_MM2:
            plan["warnings"].append(
                f"Supports are off, but {analysis['overhang_area_mm2']:g} mm² of the underside is flatter than "
                f"{analysis['support_angle']:g} degrees and will print over thin air. Turn supports on unless that is intended.")
        return {"ok": True, "project_3mf": made["project"], **result, "objects": made["objects"],
                "supports_on": supports_on, "support_sections_in_gcode": supports_printed,
                "settings_checked_in_gcode": len(expected) - len(mismatched), "settings_mismatched": mismatched,
                "plan": plan, "model": analysis}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def open_in_slicer(path: str) -> dict:
    """Open a project in the slicer's window, ready to slice and print.

    Pass the project_3mf path returned by slice_model. The slicer shows the model with all the
    chosen settings applied, and its own Slice and Print buttons work as usual. A .gcode file
    opens as a view-only preview that cannot be printed.

    Args:
        path: Absolute path to the .3mf project (preferred), or a .gcode or model file.
    """
    try:
        settings = load_settings()
        binary = settings.require_slicer()
        target = _path(path)
        if not target.is_file():
            return _fail(FileNotFoundError(f"File not found: {target}"))
        if platform.system() == "Darwin" and ".app/" in str(binary):
            app = str(binary).split(".app/")[0] + ".app"
            # -n: a new window with an empty plate. It never replaces a project already open, and
            # with an empty plate Flash Studio opens a .3mf as a project without asking
            # (Plater::open_3mf_file asks only when the plate has objects).
            subprocess.Popen(["open", "-n", "-a", app, str(target)])
        else:
            subprocess.Popen([str(binary), str(target)])
        return {"ok": True, "opened": str(target)}
    except _ERRORS + (OSError,) as exc:
        return _fail(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
