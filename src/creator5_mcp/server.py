"""MCP tools: analyse a model, recommend settings, slice, and open the result in the slicer."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image

from . import __version__, model, preview as pictures, printcheck, printer, profiles, project, recommend, results, slicecheck, slicer, spools
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
        "warnings to the user. Each result carries print_check: whether the shape will print, how it stands "
        "against the best way up, and not_judged, what the file could not be measured for. Say what was not judged; "
        "never call an unchecked part fine. print_risks names parts likely to topple or come off the plate. sliced_check compares the G-code with the model: anything that starts "
        "printing in mid-air, an object left out, a part printed smaller than modelled. slice_model also draws the plate (plate_png) and the first layer "
        "(first_layer_png): show the plate picture with every result, by calling preview with the project_3mf path, "
        "and the first layer when supports, a brim or fine detail matter. If the user gives figures from the spool's label, pass them as "
        "nozzle_temp, bed_temp and max_speed, and offer to keep them with save_spool so the spool's name is "
        "enough next time. For a multi-colour model, pass the filaments in toolheads 2 to 4 as other_filaments. "
        "Use recommend_settings first when the user wants to review choices. For a job with more than one filament, "
        "after the user presses Slice plate in Flash Studio, call check_flash_studio_slice to confirm each toolhead's "
        "temperatures. Pass finish=smooth when the user wants a smooth or professional surface. When the user says how a print "
        "came out, call record_print_result. When the user asks about the printer, or before telling them to load "
        "filament, call printer_status: it reads what the printer is doing and what is loaded in each toolhead, and "
        "changes nothing. The user prints from Flash Studio; this server never sends anything to the printer."
    ),
)

_ERRORS = (ConfigError, profiles.ProfileError, model.ModelError, recommend.RecommendError,
           slicer.SliceError, project.ProjectError, results.ResultError)


def _code_revision() -> str | None:
    """The git commit this server is running from, when it runs from a checkout.

    The server keeps its code in memory until the client restarts, so this shows whether a
    restart picked up the latest pull.
    """
    try:
        folder = Path(__file__).resolve().parent
        commit = subprocess.run(["git", "-C", str(folder), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        if commit.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", str(folder), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return commit.stdout.strip() + (" with uncommitted changes" if dirty else "")
    except (OSError, subprocess.TimeoutExpired):
        return None


_STARTED_AT_REVISION = _code_revision()


def _fail(exc: Exception) -> dict:
    return {"ok": False, "error": str(exc)}


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@mcp.tool()
def check_setup() -> dict:
    """Report whether the slicer and its Creator 5 presets are ready, and which code this server is running.

    restart_needed is true when the code on disk has changed since this server started.
    """
    settings = load_settings()
    report: dict = {
        "ok": settings.slicer_path is not None and settings.profiles_dir is not None,
        "server_version": __version__,
        "code_revision": _STARTED_AT_REVISION,
        "restart_needed": _STARTED_AT_REVISION != _code_revision(),
        "slicer_path": str(settings.slicer_path) if settings.slicer_path else None,
        "profiles_dir": str(settings.profiles_dir) if settings.profiles_dir else None,
        "output_dir": str(settings.output_dir),
        "print_check_tool": printcheck.version() or "not installed: shapes are sliced without the print check. " + printcheck.INSTALL,
    }
    if settings.slicer_path:
        report["slicer_version"] = slicer.version(settings)
        release = slicer.app_version(settings)
        report["flash_studio_version"], report["tested_with"] = release, slicer.TESTED_FLASH_STUDIO
        if release and release != slicer.TESTED_FLASH_STUDIO:
            report["warning"] = (f"Flash Studio is now {release}; this server was last tested with {slicer.TESTED_FLASH_STUDIO}. "
                                 "An update can change defaults and folders. Run the tests (pytest and scripts/smoke_test.py) "
                                 "before trusting a slice, then set TESTED_FLASH_STUDIO in slicer.py.")
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
    """Measure an STL, OBJ, 3MF or STEP: size, volume, bed contact, overhangs, parts, toolheads used and bed fit.

    Args:
        model_path: Absolute path to the model file on this computer.
    """
    try:
        return {"ok": True, **model.analyse(_path(model_path)), "print_check": printcheck.check(_path(model_path))}
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
          shape_path: Path | None = None, other_filaments: list[str] | None = None,
          finish: str = "standard") -> tuple[dict, dict | None]:
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
        analysis = model.analyse(shape_path or path, angle, float(nozzle))
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
        first["max_speed"] or None, designer, toolheads, first["max_flow"] or None, finish)
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
            if overrides:
                # Flash Studio keeps changed filament values on toolhead 1 only, and swaps anything else that is
                # not a stock preset for a generic one. So this toolhead gets a stock preset of the same material.
                # The bed is shared and the hottest request wins, so the nearest bed temperature is chosen first,
                # then the nearest nozzle temperature, which affects this toolhead alone.
                stock = profiles.filament_presets(profiles_dir, nozzle, pro)
                want_nozzle = float(overrides.get("nozzle_temperature") or stock[preset]["nozzle_temperature"])
                want_bed = float(overrides.get("textured_plate_temp") or stock[preset]["bed_temperature"])
                kind = stock[preset]["type"]
                preset = min((n for n in stock if stock[n]["type"] == kind and "CF" not in n and "GF" not in n),
                             key=lambda n: (abs(float(stock[n]["bed_temperature"]) - want_bed),
                                            abs(float(stock[n]["nozzle_temperature"]) - want_nozzle), n != preset, n))
                plan["warnings"].append(
                    f"Toolhead {i + 1}: Flash Studio keeps a spool's own figures on toolhead 1 only. It uses the stock preset "
                    f"'{preset}': nozzle {stock[preset]['nozzle_temperature']} C and bed {stock[preset]['bed_temperature']} C, "
                    f"where {want_nozzle:g} C and {want_bed:g} C were asked for.")
                overrides = {}
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
    _earlier_prints(settings, plan, shape.get("objects", []))
    if toolheads > 1:
        plan["rationale"].append("Toolheads: " + "; ".join(f"{i + 1} = {slot['material']} {slot['colour']}" for i, slot in enumerate(slots)) + ".")
    if unnamed:
        plan["warnings"].append(f"The model uses {toolheads} toolheads but {len(said)} filament{'s were' if len(said) > 1 else ' was'} named. "
                                f"The rest were given toolhead 1's filament, in the model's colours: {', '.join(unnamed)}. "
                                "Load those colours, or name each filament.")
    return plan, analysis


_CHECKS = ThreadPoolExecutor(max_workers=1)     # the print check runs beside the slice, so it adds little to the wait


def _print_check(target: Path, plan: dict, nozzle: str, wait: bool = True):
    """Check the shape that will print, with the plan's layer height and material. With wait, add what it found
    to the plan's warnings and return it; without, return the running check for _print_checked to collect."""
    running = _CHECKS.submit(printcheck.check, target, float(nozzle), float(plan["layer_height_mm"]), plan["material"])
    return _print_checked(running, plan) if wait else running


def _print_checked(running, plan: dict) -> dict:
    found = running.result()
    plan["warnings"] += [f"Print check: {text}" for text in found.get("errors", []) + found.get("warnings", [])]
    return found


def _earlier_prints(settings, plan: dict, objects: list[str]) -> None:
    """Add what the print log says about this filament preset or this model to the plan."""
    presets = {slot["preset"] for slot in plan["filaments"]}
    seen = []
    for entry in reversed(results.load(settings.output_dir)):
        used = entry.get("filament_presets") or []
        used = {re.sub(r" \(toolhead \d\)$", "", name) for name in ([used] if isinstance(used, str) else used)}
        same_model = bool(set(entry.get("objects") or []) & set(objects))
        if same_model or used & presets:
            seen.append({"date": entry.get("date"), "outcome": entry.get("outcome"), "same_model": same_model,
                         "objects": entry.get("objects"), "process_preset": entry.get("process_preset"),
                         "notes": entry.get("notes", "")})
    plan["earlier_prints"] = seen[:3]
    if seen and seen[0]["outcome"] != "good":
        what = "this model" if seen[0]["same_model"] else "this filament preset"
        plan["warnings"].append(f"The last recorded print with {what} ({seen[0]['date']}) was rated {seen[0]['outcome']}: "
                                f"{seen[0]['notes'][:300]}")
    elif seen:
        what = "this model" if seen[0]["same_model"] else "this filament preset"
        plan["rationale"].append(f"The last recorded print with {what} ({seen[0]['date']}) was rated good.")


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
    finish: str = "standard",
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
        finish: standard or smooth. Smooth uses at least fine layers and irons flat tops, for the smoothest
            surfaces the printer gives. Use it when the user asks for a smooth or professional finish.
    """
    try:
        plan, analysis = _plan(model_path, filament, quality, strength, nozzle, supports, brim,
                               layer_height, precise_fit, smooth_top, pro, nozzle_temp, bed_temp, max_speed,
                               other_filaments=other_filaments, finish=finish)
        check = _print_check(_path(model_path), plan, nozzle) if model_path else None
        return {"ok": True, "plan": plan, "model": analysis, "print_check": check}
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
    pause_before_z: float = 0,
    finish: str = "standard",
) -> dict:
    """Slice a model for the Creator 5 with settings chosen for the filament and purpose.

    Writes a project file (project_3mf) to open in the slicer and print from, then slices that same
    project to check it: print time, filament use, and that every chosen setting reached the G-code.
    Returns those, the settings applied with reasons, and any warnings to pass on to the user.
    A 3MF that carries its designer's slicer settings keeps the designer's supports and brim.
    A multi-colour 3MF keeps its objects, parts and toolhead assignments.

    Args:
        model_path: Absolute path to an STL, 3MF, OBJ or STEP file on this computer.
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
        pause_before_z: Pause the print before the layer whose top is at this height in mm, to embed a magnet,
            nut or NFC tag. 0 for no pause. The layer below it is the last one printed before the pause.
        finish: standard or smooth. Smooth uses at least fine layers and irons flat tops, for the smoothest
            surfaces the printer gives. Use it when the user asks for a smooth or professional finish.
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
        stamp = time.strftime("%Y%m%d_%H%M%S")
        converted = holding = None
        if source.suffix.lower() in model.CAD_SUFFIXES:
            # Converted once; everything after this works from the mesh. It moves into the job folder once that is named.
            holding = settings.output_dir / f"{stamp}_converting"
            converted = model.convert_step(source, holding)
            notes.append("The STEP file was converted to a mesh (0.01 mm tolerance).")
        loaded = model.load_objects(converted or source)
        # A downloaded file is often named by a hash; the name its designer gave the part reads better.
        title = loaded["objects"][0]["name"] if source.suffix.lower() == ".3mf" else source.stem
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", job_name or title).strip("_") or "print"
        job_dir = settings.output_dir / f"{stamp}_{name}"
        job_dir.mkdir(parents=True, exist_ok=True)
        if converted:
            converted = converted.rename(job_dir / converted.name)
            holding.rmdir()

        # Designer settings come from the original file; the shape from the file that will print.
        shaped = converted or source
        several = len(loaded["objects"]) > 1 or len(loaded["objects"][0]["parts"]) > 1
        if auto_orient and several:
            notes.append("Orientation left as designed: turning a model of several parts would merge them into one.")
        elif auto_orient:
            shaped = slicer.orient(settings, shaped, job_dir, name)
            notes.append("The slicer chose the orientation; the project and the G-code both use it.")
        plan, analysis = _plan(str(source), filament, quality, strength, nozzle, supports, brim, layer_height,
                               precise_fit, smooth_top, pro, nozzle_temp, bed_temp, max_speed, shape_path=shaped,
                               other_filaments=other_filaments, finish=finish)
        # A STEP is checked as the STEP, which is exact; a turned copy is checked as it now stands.
        checking = _print_check(source if converted and shaped == converted else shaped, plan, nozzle, wait=False)
        made = project.write_project(profiles_dir, shaped, plan, job_dir / f"{name}.3mf", copies,
                                     process_overrides, filament_overrides, pause_before_z=pause_before_z)
        result = slicer.slice_project(settings, Path(made["project"]), job_dir)
        result["notes"] = notes + result["notes"]
        chosen = {**plan["process_overrides"], **(process_overrides or {}), **(filament_overrides or {}),
                  **plan["filaments"][0]["overrides"]}
        expected = {k: made["settings"][k] for k in chosen}
        mismatched = slicer.check_settings(Path(result["gcode"]), expected)
        if mismatched:
            plan["warnings"].append("Some settings did not reach the G-code: " + "; ".join(mismatched))
        if made["unknown_override_keys"]:
            plan["warnings"].append(
                "These override keys are not in the base preset and may be misspelt: "
                + ", ".join(made["unknown_override_keys"]))
        if made["preset_only_process_settings"]:
            plan["warnings"].append(
                "Flash Studio can reset these to the preset's values when it signs in after opening the project; "
                "check them under Process, Global before printing: " + ", ".join(made["preset_only_process_settings"]))
        paused_at = slicer.pauses(Path(result["gcode"]))
        if pause_before_z:
            if not made["settings"].get("machine_pause_gcode"):
                plan["warnings"].append("The printer preset has no pause command, so the pause will do nothing.")
            if [round(z, 3) for z in paused_at] != [round(pause_before_z, 3)]:
                plan["warnings"].append(f"A pause before {pause_before_z:g} mm was asked for, but the G-code pauses at "
                                        f"{paused_at or 'no height'}. Pick a height that is the top of a layer.")
            else:
                result["notes"].append(f"The print pauses before the layer at {pause_before_z:g} mm, with the last layer printed "
                                       f"ending {made['settings'].get('layer_height')} mm below. Resume from the printer when ready.")
        # One bed, one temperature: the slicer heats it to the highest any toolhead asks for.
        beds = [float(v) for v in made["settings"].get("textured_plate_temp", [])]
        if beds and max(beds) - min(beds) >= 10:
            cool = [str(i + 1) for i, v in enumerate(beds) if max(beds) - v >= 10]
            plan["warnings"].append(
                f"The bed will run at {max(beds):g} C, the highest any toolhead asks for ({', '.join(f'{v:g}' for v in beds)} C by toolhead). "
                f"That is too hot for the filament in toolhead {' and '.join(cool)}, which may soften and lose its shape near the bed. "
                "Use filaments that want a similar bed temperature, or pass bed_temp to choose one.")
        # The setting says what was asked for; the G-code says what will print.
        supports_printed = slicer.support_sections(Path(result["gcode"]))
        supports_on = made["settings"].get("enable_support") == "1"
        if supports_on and not supports_printed:
            result["notes"].append("Supports are on, but the slicer found nothing that needs them, so none will print.")
        elif not supports_on and analysis.get("overhang_area_mm2", 0) > recommend.OVERHANG_MM2:
            plan["warnings"].append(
                f"Supports are off, but {analysis['overhang_area_mm2']:g} mm² of the underside is flatter than "
                f"{analysis['support_angle']:g} degrees and will print over thin air. Turn supports on unless that is intended.")
        drawn = _draw(Path(made["project"]), Path(result["gcode"]), job_dir, result["notes"])
        sliced = _sliced(Path(made["project"]), Path(result["gcode"]), plan)
        risks = recommend.print_risks(model.object_shapes(model.load_objects(Path(made["project"]))), made["settings"],
                                      result["summary"].get("print_time_seconds", 0))
        plan["warnings"] += [f"Print risk: {text}" for text in risks]
        check = _print_checked(checking, plan)
        return {"ok": True, "project_3mf": made["project"], **result, **drawn, "objects": made["objects"],
                "supports_on": supports_on, "support_sections_in_gcode": supports_printed, "pauses_at_z": paused_at,
                "settings_checked_in_gcode": len(expected) - len(mismatched), "settings_mismatched": mismatched,
                "plan": plan, "model": analysis, "print_check": check, "sliced_check": sliced, "print_risks": risks}
    except _ERRORS as exc:
        return _fail(exc)


def _sliced(project: Path, gcode: Path, plan: dict) -> dict:
    """Compare the G-code with the project and add what was found to the plan's warnings."""
    try:
        found = slicecheck.check(project, gcode)
        plan["warnings"] += [f"Sliced check: {text}" for text in found["warnings"]]
        return found
    except Exception as exc:   # a check that cannot run is said, and the slice still stands
        return {"checked": False, "reason": f"The sliced result could not be checked: {exc}"}


def _layer_picture(project: Path, gcode: Path, layer: int = 1, z: float = 0) -> bytes:
    found = slicer.layer_moves(gcode, layer, z)
    title = f"layer {found['layer']} at {found['z']:g} mm"
    return pictures.layer_png(found["moves"], model.load_objects(project)["colours"], title)


def _draw(project: Path, gcode: Path, job_dir: Path, notes: list[str]) -> dict:
    """Write the plate and first-layer pictures beside the project. A picture that fails is a note, never a failed slice."""
    out = {"plate_png": None, "first_layer_png": None}
    try:
        png, said = pictures.plate_png(project, zoom=pictures.fills_little(project))     # small parts fill the picture
        (job_dir / "plate.png").write_bytes(png)
        out["plate_png"] = str(job_dir / "plate.png")
        notes += said
        (job_dir / "first_layer.png").write_bytes(_layer_picture(project, gcode))
        out["first_layer_png"] = str(job_dir / "first_layer.png")
    except Exception as exc:   # drawing is a courtesy: nothing it can raise should lose a good slice
        notes.append(f"A preview picture could not be drawn: {exc}")
    return out


@mcp.tool()
def preview(project_3mf: str, view: str = "iso", layer: int = 0, z: float = 0, zoom: bool = False):
    """A picture of what slice_model made: the plate, or one sliced layer seen from above.

    With no layer and no z, the plate: the bed and every part in its toolhead's colour, painted faces in
    their painted colours, as the project file holds it. With layer or z, that layer of the job's G-code: each extrusion in its toolhead's
    colour, with supports, brim and skirt paler. Show the plate after every slice.

    Args:
        project_3mf: The project_3mf path slice_model returned.
        view: For the plate: iso (from the front left, looking down) or top.
        zoom: For the plate: True frames the parts, False shows the whole bed and where they stand on it.
        layer: Layer number, 1 for the first. 0 for the plate.
        z: Height in mm, instead of a layer number: the first layer whose top is at or above it.
    """
    try:
        target = _path(project_3mf)
        if not layer and not z:
            return Image(data=pictures.plate_png(target, view, zoom=zoom)[0], format="png")
        gcodes = sorted(target.parent.glob("*.gcode"))
        if not gcodes:
            return _fail(FileNotFoundError(f"No G-code beside {target.name}. Slice the model with slice_model first."))
        return Image(data=_layer_picture(target, gcodes[0], layer or 1, z), format="png")
    except _ERRORS + (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        return _fail(exc)


@mcp.tool()
def check_flash_studio_slice(project_3mf: str) -> dict:
    """Read what Flash Studio's own window sliced for a project, after the user pressed Slice plate there.

    This is the proof of what will print: the nozzle temperature and preset of each toolhead, the bed
    temperature, pauses, time and filament, and every changed setting that differs from the project.
    Use it for multi-filament jobs, where Flash Studio may swap the filament in toolheads 2 to 4.
    The window must still be open; nothing is sent to the printer.

    Args:
        project_3mf: The project_3mf path slice_model returned, as opened in Flash Studio.
    """
    try:
        target = _path(project_3mf)
        gcode = slicer.studio_gcode(target)
        if gcode is None:
            return {"ok": False, "error": "Flash Studio has not sliced this project yet, or its window was closed. "
                                          "Open the project, press Slice plate, leave the window open, then ask again."}
        with zipfile.ZipFile(target) as archive:
            asked = json.loads(archive.read("Metadata/project_settings.config"))
        return {"ok": True, **slicer.studio_report(gcode, asked)}
    except _ERRORS + (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        return _fail(exc)


@mcp.tool()
def printer_status() -> dict:
    """What the Creator 5 is doing now, read over the home network. Read-only: nothing is sent to the printer but the question.

    Finds the printer by itself. Returns its state, the file printing with progress and time left, each
    toolhead's temperature and the bed's, and the material and colour loaded in each toolhead.
    When the status cannot be read, message says why and what to do: the printer answers only in
    LAN-only mode, and needs its check code stored once in the Mac's Keychain.
    """
    return {"ok": True, **printer.status()}


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
