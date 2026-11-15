"""MCP tools: analyse a model, recommend settings, slice, and send to the Creator 5."""

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

from . import __version__, model, printer, profiles, project, recommend, slicer
from .config import ConfigError, load_settings

mcp = FastMCP(
    "creator5-slicer",
    instructions=(
        "Slices 3D models for a Flashforge Creator 5 using OrcaSlicer. Typical flow: call "
        "slice_model with the model path, the filament the user named, and a quality and strength "
        "level that suit the part's purpose. It analyses the shape, picks presets and settings, "
        "writes a project file, slices that project to check it, and returns time and filament estimates. "
        "Then, without asking, call open_in_slicer with the project_3mf path so the user can press Slice "
        "and Print in Flash Studio. Pass auto_orient only when the user asks for it. Relay the returned "
        "warnings to the user. If the user gives figures from the spool's label, pass them as "
        "nozzle_temp, bed_temp and max_speed. Use recommend_settings first when the user wants to review choices. "
        "Only call send_to_printer with start_print=true after the user confirms the printer is "
        "clear and loaded with the right filament."
    ),
)

_ERRORS = (ConfigError, profiles.ProfileError, model.ModelError, recommend.RecommendError,
           slicer.SliceError, printer.PrinterError, project.ProjectError)


def _fail(exc: Exception) -> dict:
    return {"ok": False, "error": str(exc)}


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@mcp.tool()
def check_setup() -> dict:
    """Report whether the slicer, its Creator 5 presets and the printer connection are ready."""
    settings = load_settings()
    report: dict = {
        "ok": settings.slicer_path is not None and settings.profiles_dir is not None,
        "server_version": __version__,
        "slicer_path": str(settings.slicer_path) if settings.slicer_path else None,
        "profiles_dir": str(settings.profiles_dir) if settings.profiles_dir else None,
        "output_dir": str(settings.output_dir),
        "printer_configured": bool(settings.printer_ip and settings.printer_serial and settings.printer_check_code),
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
    """List the filament types this server understands and the Creator 5 presets installed.

    Args:
        nozzle: Nozzle diameter in mm: 0.25, 0.4, 0.6 or 0.8.
        pro: True for the Creator 5 Pro.
    """
    try:
        settings = load_settings()
        return {
            "ok": True,
            "understood_types": [m.label for m in recommend.MATERIALS],
            "installed_presets": profiles.filament_presets(settings.require_profiles(), nozzle, pro),
            "quality_levels": {k: v[1] for k, v in recommend.QUALITY.items()},
            "strength_levels": {k: v.why for k, v in recommend.STRENGTH.items()},
        }
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def analyse_model(model_path: str) -> dict:
    """Measure an STL: size, volume, bed contact, overhangs and whether it fits the 256 mm bed.

    Args:
        model_path: Absolute path to the model file on this computer.
    """
    try:
        return {"ok": True, **model.analyse(_path(model_path))}
    except _ERRORS as exc:
        return _fail(exc)


def _plan(model_path: str | None, filament: str, quality: str, strength: str, nozzle: str, supports: str,
          brim: str, layer_height: float | None, precise_fit: bool, smooth_top: bool, pro: bool,
          nozzle_temp: int = 0, bed_temp: int = 0, max_speed: int = 0,
          shape_path: Path | None = None) -> tuple[dict, dict | None]:
    """shape_path: the file whose shape will print, when it differs from model_path (an oriented copy)."""
    settings = load_settings()
    analysis = designer = None
    if model_path:
        path = _path(model_path)
        designer = model.designer_settings(path)
        angle = float(designer.get("support_threshold_angle") or 0) or model.DEFAULT_SUPPORT_ANGLE
        analysis = model.analyse(shape_path or path, angle)
    plan = recommend.recommend(
        settings.require_profiles(), filament, quality, strength, nozzle, analysis, supports, brim,
        layer_height or None, precise_fit, smooth_top, pro, nozzle_temp or None, bed_temp or None, max_speed or None,
        designer)
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
) -> dict:
    """Choose presets and settings for a print without slicing, with the reasoning for each choice.

    Args:
        filament: Filament as the user describes it, e.g. "black PLA", "PETG", "TPU 95A", "silk PLA".
        model_path: Optional absolute path to the STL, OBJ or 3MF, so supports and brim respond to its shape
            and to any settings its designer saved in the 3MF.
        quality: draft, standard or fine. Sets layer height and outer wall care.
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
    """
    try:
        plan, analysis = _plan(model_path, filament, quality, strength, nozzle, supports, brim,
                               layer_height, precise_fit, smooth_top, pro, nozzle_temp, bed_temp, max_speed)
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
) -> dict:
    """Slice a model for the Creator 5 with settings chosen for the filament and purpose.

    Writes a project file (project_3mf) to open in the slicer and print from, then slices that same
    project to check it: print time, filament use, and that every chosen setting reached the G-code.
    Returns those, the settings applied with reasons, and any warnings to pass on to the user.
    A 3MF that carries its designer's slicer settings keeps the designer's supports and brim.

    Args:
        model_path: Absolute path to an STL, 3MF or OBJ file on this computer.
        filament: Filament as the user describes it, e.g. "black PLA", "PETG", "TPU 95A".
        quality: draft, standard or fine.
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
        filament_overrides: Extra OrcaSlicer filament settings, e.g. {"nozzle_temperature": 215}.
        job_name: Name for the output folder and files.
        pro: True for the Creator 5 Pro.
        nozzle_temp: Nozzle temperature in C from the spool's label, or 0 to use the preset.
        bed_temp: Bed temperature in C from the spool's label, or 0 to use the preset.
        max_speed: Fastest print speed in mm/s the spool's label recommends, or 0 for no extra limit.
    """
    try:
        settings = load_settings()
        profiles_dir = settings.require_profiles()
        source = _path(model_path)
        if not source.is_file():
            raise model.ModelError(f"Model not found: {source}")
        if copies < 1:
            raise project.ProjectError("copies must be at least 1")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", job_name or source.stem).strip("_") or "print"
        job_dir = settings.output_dir / f"{stamp}_{name}"
        job_dir.mkdir(parents=True, exist_ok=True)
        notes: list[str] = []
        # Designer settings come from the original file; the shape from the file that will print.
        shaped = slicer.orient(settings, source, job_dir) if auto_orient else source
        if auto_orient:
            notes.append("The slicer chose the orientation; the project and the G-code both use it.")
        plan, analysis = _plan(str(source), filament, quality, strength, nozzle, supports, brim, layer_height,
                               precise_fit, smooth_top, pro, nozzle_temp, bed_temp, max_speed, shape_path=shaped)
        made = project.write_project(profiles_dir, shaped, plan, job_dir / f"{name}.3mf", copies,
                                     process_overrides, filament_overrides, project.colour_for(filament))
        result = slicer.slice_project(settings, Path(made["project"]), job_dir)
        result["notes"] = notes + result["notes"]
        expected = {k: made["settings"][k] for k in
                    {**plan["process_overrides"], **(process_overrides or {}),
                     **plan["filament_overrides"], **(filament_overrides or {})}}
        mismatched = slicer.check_settings(Path(result["gcode"]), expected)
        if mismatched:
            plan["warnings"].append("Some settings did not reach the G-code: " + "; ".join(mismatched))
        if made["unknown_override_keys"]:
            plan["warnings"].append(
                "These override keys are not in the base preset and may be misspelt: "
                + ", ".join(made["unknown_override_keys"]))
        return {"ok": True, "project_3mf": made["project"], **result,
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


@mcp.tool()
def printer_status() -> dict:
    """Read the Creator 5's current state over the local network (experimental)."""
    try:
        return {"ok": True, "status": printer.status(load_settings())}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def send_to_printer(gcode_path: str, start_print: bool = False, level_bed: bool = True) -> dict:
    """Upload a sliced file to the Creator 5 over the local network (experimental).

    By default the file is only uploaded. Set start_print=True only after the user has
    confirmed the bed is clear and the right filament is loaded in toolhead 1.

    Args:
        gcode_path: Absolute path to the .gcode or .3mf produced by slice_model.
        start_print: Start printing straight after upload.
        level_bed: Run bed levelling before the print starts.
    """
    try:
        return {"ok": True, **printer.upload(load_settings(), _path(gcode_path), start_print, level_bed)}
    except _ERRORS as exc:
        return _fail(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
