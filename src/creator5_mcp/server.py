"""MCP tools: analyse a model, recommend settings, slice, and send to the Creator 5."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from . import __version__, model, printer, profiles, recommend, slicer
from .config import ConfigError, load_settings

mcp = FastMCP(
    "creator5-slicer",
    instructions=(
        "Slices 3D models for a Flashforge Creator 5 using OrcaSlicer. Typical flow: call "
        "slice_model with the model path, the filament the user named, and a quality and strength "
        "level that suit the part's purpose. It analyses the shape, picks presets and settings, "
        "slices, and returns the G-code path with time and filament estimates. Relay the returned "
        "warnings to the user. Use recommend_settings first when the user wants to review choices. "
        "Only call send_to_printer with start_print=true after the user confirms the printer is "
        "clear and loaded with the right filament."
    ),
)

_ERRORS = (ConfigError, profiles.ProfileError, model.ModelError, recommend.RecommendError,
           slicer.SliceError, printer.PrinterError)


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
          brim: str, layer_height: float | None, precise_fit: bool, smooth_top: bool, pro: bool) -> tuple[dict, dict | None]:
    settings = load_settings()
    analysis = model.analyse(_path(model_path)) if model_path else None
    plan = recommend.recommend(
        settings.require_profiles(), filament, quality, strength, nozzle, analysis, supports, brim,
        layer_height or None, precise_fit, smooth_top, pro)
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
) -> dict:
    """Choose presets and settings for a print without slicing, with the reasoning for each choice.

    Args:
        filament: Filament as the user describes it, e.g. "black PLA", "PETG", "TPU 95A", "silk PLA".
        model_path: Optional absolute path to the STL, so supports and brim respond to its shape.
        quality: draft, standard or fine. Sets layer height and outer wall care.
        strength: decorative, standard, functional or structural. Sets walls, infill and shells.
        nozzle: Nozzle diameter in mm: 0.25, 0.4, 0.6 or 0.8.
        supports: auto, on or off.
        brim: auto, on or off.
        layer_height: Exact layer height in mm, or 0 to let quality decide.
        precise_fit: True for parts that must mate with other parts.
        smooth_top: True to iron flat top surfaces.
        pro: True for the Creator 5 Pro.
    """
    try:
        plan, analysis = _plan(model_path, filament, quality, strength, nozzle, supports, brim,
                               layer_height, precise_fit, smooth_top, pro)
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
) -> dict:
    """Slice a model for the Creator 5 with settings chosen for the filament and purpose.

    Returns the G-code path, a 3MF project to open in the slicer, print time, filament use,
    the settings applied with reasons, and any warnings to pass on to the user.

    Args:
        model_path: Absolute path to an STL, 3MF, STEP or OBJ file on this computer.
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
        auto_orient: Let the slicer rotate the part. Leave False when the model is already oriented for strength.
        process_overrides: Extra OrcaSlicer process settings applied last, e.g. {"wall_loops": 5}.
        filament_overrides: Extra OrcaSlicer filament settings, e.g. {"nozzle_temperature": 215}.
        job_name: Name for the output folder and files.
        pro: True for the Creator 5 Pro.
    """
    try:
        settings = load_settings()
        plan, analysis = _plan(model_path, filament, quality, strength, nozzle, supports, brim,
                               layer_height, precise_fit, smooth_top, pro)
        result = slicer.slice_model(settings, _path(model_path), plan, process_overrides, filament_overrides,
                                    job_name, auto_orient, copies)
        if result["settings_not_in_base_preset"]:
            plan["warnings"].append(
                "These override keys are not in the base preset and may be misspelt: "
                + ", ".join(result["settings_not_in_base_preset"]))
        return {"ok": True, **result, "plan": plan, "model": analysis}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def open_in_slicer(path: str) -> dict:
    """Open a sliced project or model in the slicer's window so the user can inspect it and print from there.

    Args:
        path: Absolute path to a .3mf, .gcode or model file.
    """
    try:
        settings = load_settings()
        binary = settings.require_slicer()
        target = _path(path)
        if not target.is_file():
            return _fail(FileNotFoundError(f"File not found: {target}"))
        if platform.system() == "Darwin" and ".app/" in str(binary):
            app = str(binary).split(".app/")[0] + ".app"
            subprocess.Popen(["open", "-a", app, str(target)])
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
