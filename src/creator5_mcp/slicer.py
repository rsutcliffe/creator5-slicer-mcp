"""Run the slicer's command line on a project file and read back what it produced."""

from __future__ import annotations

import math
import os
import plistlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .config import Settings


class SliceError(RuntimeError):
    pass


_END_MARKERS = ("; EXECUTABLE_BLOCK_END", ";end_gcode", "; CONFIG_BLOCK_END", "; filament used [g]")


def gcode_is_complete(path: Path) -> bool:
    """True when the G-code file was written through to its end.

    Some slicer builds finish the G-code and then crash while shutting down,
    so the exit code alone cannot say whether the file is usable.
    """
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(200_000)
        handle.seek(max(0, size - 200_000))
        tail = handle.read()
    text = (head + tail).decode("utf-8", "ignore")
    return _END_MARKERS[0] in tail.decode("utf-8", "ignore") or all(m in text for m in _END_MARKERS[1:])


_TIME = re.compile(r";\s*(?:total estimated time|estimated printing time \(normal mode\))\s*[:=]\s*([0-9dhms ]+)", re.I)
_FIELDS = {
    # One value per toolhead, comma-separated; the summary reports their total.
    "filament_used_g": re.compile(r";\s*(?:total )?filament used \[g\]\s*[:=]\s*([0-9., ]+)", re.I),
    "filament_used_mm": re.compile(r";\s*(?:total )?filament used \[mm\]\s*[:=]\s*([0-9., ]+)", re.I),
    "filament_used_cm3": re.compile(r";\s*(?:total )?filament used \[cm3\]\s*[:=]\s*([0-9., ]+)", re.I),
    "layers": re.compile(r";\s*total layer(?:s count| number)\s*[:=]\s*([0-9]+)", re.I),
}


def _seconds(text: str) -> int:
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def parse_gcode_summary(path: Path) -> dict:
    """Pull the time and filament estimates from a G-code file's comments."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(200_000)
        tail = b""
        if size > 200_000:
            handle.seek(max(200_000, size - 400_000))
            tail = handle.read()
    text = (head + b"\n" + tail).decode("utf-8", "ignore")
    out: dict = {}
    match = _TIME.search(text)
    if match:
        seconds = _seconds(match.group(1))
        out["print_time_seconds"] = seconds
        out["print_time"] = f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    for key, pattern in _FIELDS.items():
        match = pattern.search(text)
        if match:
            values = [float(v) for v in match.group(1).split(",") if v.strip()]
            out[key] = int(values[0]) if key == "layers" else round(sum(values), 2)
            if key == "filament_used_g" and len(values) > 1:
                out["filament_used_g_per_toolhead"] = values
    return out


def version(settings: Settings) -> str:
    try:
        result = subprocess.run([str(settings.require_slicer()), "--help"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable ({exc})"
    first = (result.stdout or result.stderr).strip().splitlines()
    return first[0] if first else "unknown"


TESTED_FLASH_STUDIO = "1.7.22"   # the Flash Studio release the real-slicer tests last passed against


def app_version(settings: Settings) -> str | None:
    """The release number Flash Studio shows its user (macOS app bundle), or None where it cannot be read."""
    binary = str(settings.slicer_path or "")
    if ".app/" not in binary:
        return None
    try:
        with open(binary.split(".app/")[0] + ".app/Contents/Info.plist", "rb") as handle:
            return plistlib.load(handle).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None


def studio_gcode(project: Path, roots: list[Path] | None = None) -> Path | None:
    """The G-code Flash Studio's own window made when the user pressed Slice on this project, newest first.

    Each open window keeps a session folder holding origin.txt (the project it opened) and
    Metadata/.<pid>.0.gcode. Flash Studio 1.7.22 keeps them under its recovery folder; 1.7 kept
    them in the temporary folder. The folder goes when the window closes.
    """
    if roots is None:
        roots = [Path.home() / "Library" / "Application Support" / "Orca-Flashforge" / "recovery",
                 Path(os.environ.get("APPDATA", "")) / "Orca-Flashforge" / "recovery",
                 Path(tempfile.gettempdir()) / "orca-flashforge" / "model"]
    found = []
    for root in roots:
        for origin in list(root.glob("*/origin.txt")) + list(root.glob("*/*/origin.txt")):
            try:
                same = Path(origin.read_text(encoding="utf-8", errors="ignore").strip()).resolve() == project.resolve()
            except OSError:
                continue
            if same:
                found += [g for g in (origin.parent / "Metadata").glob(".*.gcode") if gcode_is_complete(g)]
    return max(found, key=lambda g: g.stat().st_mtime, default=None)


def studio_report(gcode: Path, project_settings: dict) -> dict:
    """What Flash Studio's own slice will print, and where it differs from the project it was given."""
    found = settings_in_gcode(gcode)

    def per_toolhead(key: str) -> list[str]:
        return [v.strip().strip('"') for v in re.split(r"[;,]", found.get(key, ""))]

    summary = parse_gcode_summary(gcode)
    grams = summary.get("filament_used_g_per_toolhead") or [summary.get("filament_used_g", 0)]
    sliced_by, bed = None, None
    with gcode.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith("; generated by"):
                sliced_by = line[2:].strip()
            elif line.startswith("M140 S") or line.startswith("M190 S"):
                bed = float(re.match(r"M1[49]0 S([0-9.]+)", line).group(1))
                break
    presets, nozzles, beds = per_toolhead("filament_settings_id"), per_toolhead("nozzle_temperature"), per_toolhead("textured_plate_temp")
    toolheads = [{"toolhead": i + 1, "preset": presets[i], "nozzle_c": float(nozzles[i]), "bed_asked_c": float(beds[i]), "grams": g}
                 for i, g in enumerate(grams) if g > 0 and i < len(presets)]
    changed = {key for group in project_settings.get("different_settings_to_system", []) for key in group.split(";") if key}
    differs = []
    for key in sorted(changed | {"print_settings_id", "filament_settings_id", "nozzle_temperature", "textured_plate_temp"}):
        if key not in project_settings:
            continue
        asked = _norm(project_settings[key]).split(",")
        got = _norm(found.get(key, "missing")).split(",")[:len(asked)]     # the slicer pads lists to four toolheads
        if asked != got:
            differs.append(f"{key}: project has {','.join(asked)}, Flash Studio sliced {','.join(got)}")
    return {"gcode": str(gcode), "sliced_by": sliced_by,
            "sliced_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(gcode.stat().st_mtime)),
            "toolheads": toolheads, "bed_set_to_c": bed, "pauses_at_z": pauses(gcode), "summary": summary,
            "differs_from_project": differs}


def _run(settings: Settings, args: list[str], job_dir: Path, log_name: str, timeout: int) -> subprocess.CompletedProcess:
    job_dir.mkdir(parents=True, exist_ok=True)
    args = [str(settings.require_slicer()), *args]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SliceError(f"Slicer timed out after {timeout} s") from exc
    (job_dir / log_name).write_text(
        "$ " + " ".join(args) + f"\n\nexit code {result.returncode}\n\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
        encoding="utf-8")
    return result


def orient(settings: Settings, model: Path, job_dir: Path, name: str = "model", timeout: int = 300) -> Path:
    """Let the slicer turn the model to its best printing orientation, and return it as an STL.

    The project and the G-code are then built from this one file, so both use the same orientation.
    (The command line reads STL, OBJ and 3MF only; it cannot convert STEP.)
    """
    out = job_dir / "oriented"
    out.mkdir(parents=True, exist_ok=True)   # with an OBJ input the slicer does not create it, and aborts
    result = _run(settings, ["--orient", "1", "--arrange", "0", "--export-stl", "--outputdir", str(out), str(model)],
                  job_dir, "orient.log", timeout)
    stls = sorted(out.rglob("*.stl")) if out.is_dir() else []
    if not stls:
        raise SliceError(f"The slicer could not orient the model (exit code {result.returncode}). "
                         f"Log: {job_dir / 'orient.log'}")
    target = out / f"{name}.stl"      # the slicer names it after its own object; ours is the job's name
    if stls[0] != target:
        stls[0].rename(target)
    return target


def slice_project(settings: Settings, project: Path, job_dir: Path, timeout: int = 900) -> dict:
    """Slice the project file exactly as written, so the G-code matches what the slicer opens.

    No --load-settings: the slicer takes every setting from the project. No --export-3mf: it
    makes the slicer render thumbnails through OpenGL, which crashes without a window (macOS).
    """
    result = _run(settings, ["--orient", "0", "--arrange", "0", "--slice", "0", "--outputdir", str(job_dir), str(project)],
                  job_dir, "slicer.log", timeout)
    gcodes = sorted(job_dir.rglob("*.gcode"))
    tail = (result.stderr or result.stdout).strip()[-1500:]
    # The slicer carries on with its own defaults when it cannot read the project's settings,
    # and still exits cleanly. That G-code is not the job that was asked for.
    rejected = next((line for line in (result.stdout + result.stderr).splitlines() if "load_from_json" in line and "error" in line), None)
    if rejected:
        raise SliceError(f"The slicer could not read the project's settings and would have used its defaults. "
                         f"Please report this. Log: {job_dir / 'slicer.log'}\n{rejected.strip()[-300:]}")
    if not gcodes:
        raise SliceError(f"Slicer exited with code {result.returncode} and wrote no G-code. Log: {job_dir / 'slicer.log'}\n{tail}")
    complete = gcode_is_complete(gcodes[0])
    if not complete:
        raise SliceError(f"Slicer exited with code {result.returncode} and left the G-code unfinished. "
                         f"Do not print it. Log: {job_dir / 'slicer.log'}\n{tail}")
    notes = []
    if result.returncode != 0:
        notes.append(f"The slicer finished the G-code, then exited with code {result.returncode}. "
                     "The file is complete; the log has the details.")
    return {
        "job_dir": str(job_dir),
        "gcode": str(gcodes[0]),
        "gcode_complete": complete,
        "exit_code": result.returncode,
        "notes": notes,
        "summary": parse_gcode_summary(gcodes[0]),
        "log": str(job_dir / "slicer.log"),
    }


def _norm(value) -> str:
    """A setting's values, one per toolhead, in a form that compares equal however it was written."""
    parts = value if isinstance(value, list) else re.split(r"[;,]", str(value))
    out = []
    for text in parts:
        text = str(text).strip().strip('"')
        try:
            text = f"{float(text.rstrip('%')):g}" + ("%" if text.endswith("%") else "")
        except ValueError:
            pass
        out.append(text)
    return ",".join(out)


def support_sections(gcode: Path) -> int:
    """How many stretches of support the slicer actually printed (0 means none anywhere)."""
    with gcode.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for line in handle if line.startswith(";TYPE:Support"))


def pauses(gcode: Path) -> list[float]:
    """The layer heights (top of layer, mm) the print pauses before."""
    found, z = [], 0.0
    with gcode.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(";Z:"):
                z = float(line[3:])
            elif line.startswith(";PAUSE_PRINT"):
                found.append(z)
    return found


_WORD = re.compile(r"([XYEIJ])(-?\d*\.?\d+)")


def layer_moves(gcode: Path, layer: int = 1, z: float = 0) -> dict:
    """The extruding moves of one layer: {"layer", "z", "moves": [(x0, y0, x1, y1, toolhead, feature)]}.

    layer counts from 1. With z, the layer is the first whose top is at or above that height in mm.
    toolhead counts from 0, as T0 to T3 do. feature is the slicer's own name for what is being printed
    (Outer wall, Support, Brim). Arcs are stepped into short lines. Start-up moves before the first
    layer, and the slicer's Custom sections, are left out.
    """
    x = y = e = 0.0
    tool, feature, relative = 0, "", True
    number, height, moves = 0, 0.0, []
    wanted = False
    with gcode.open(errors="ignore") as handle:
        for line in handle:
            head = line[:2]
            if head == ";L" and line.startswith(";LAYER_CHANGE"):
                if wanted:
                    break
                number += 1
                wanted = bool(not z and number == layer)
            elif head == ";Z" and line.startswith(";Z:") and number:
                height = float(line[3:])
                wanted = wanted or bool(z and height >= z - 1e-6)
            elif head == ";T" and line.startswith(";TYPE:"):
                feature = line[6:].strip()
            elif head[0] == "T" and line[1:2].isdigit():
                tool = int(line[1:].split()[0].split(";")[0])
            elif head in ("M8",) and line[:3] in ("M82", "M83"):
                relative = line[:3] == "M83"
            elif head == "G9" and line.startswith("G92"):
                words = dict(_WORD.findall(line.split(";")[0]))
                e = float(words.get("E", e))
            elif head in ("G0", "G1", "G2", "G3") and line[2:3] in (" ", "\n"):
                words = dict(_WORD.findall(line.split(";")[0]))
                nx, ny = float(words.get("X", x)), float(words.get("Y", y))
                pushed = "E" in words and (float(words["E"]) > 0 if relative else float(words["E"]) > e)
                if "E" in words and not relative:
                    e = float(words["E"])
                if wanted and pushed and feature != "Custom" and (nx != x or ny != y):
                    points = [(x, y), (nx, ny)]
                    if head in ("G2", "G3") and ("I" in words or "J" in words):
                        cx, cy = x + float(words.get("I", 0)), y + float(words.get("J", 0))
                        radius = math.hypot(x - cx, y - cy)
                        start, end = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                        sweep = (end - start) % (2 * math.pi) if head == "G3" else -((start - end) % (2 * math.pi))
                        steps = max(2, int(abs(sweep) * radius / 0.5))
                        points = [(cx + radius * math.cos(start + sweep * k / steps), cy + radius * math.sin(start + sweep * k / steps))
                                  for k in range(steps)] + [(nx, ny)]
                    moves += [(a[0], a[1], b[0], b[1], tool, feature) for a, b in zip(points, points[1:])]
                x, y = nx, ny
    if not wanted:
        raise SliceError(f"The G-code has {number} layers, so there is no layer {layer}." if not z
                         else f"The G-code ends at {height:g} mm, below {z:g} mm.")
    return {"layer": number, "z": height, "moves": moves}


def settings_in_gcode(gcode: Path) -> dict[str, str]:
    """The settings block the slicer writes at the top of the G-code."""
    out: dict[str, str] = {}
    in_block = False
    with gcode.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith("; CONFIG_BLOCK_START"):
                in_block = True
            elif line.startswith("; CONFIG_BLOCK_END"):
                break
            elif in_block and " = " in line:
                key, _, value = line[2:].rstrip("\n").partition(" = ")
                out[key] = value
    return out


def check_settings(gcode: Path, expected: dict) -> list[str]:
    """Keys whose value in the G-code differs from what the project asked for."""
    found = settings_in_gcode(gcode)
    if not found:
        return ["The G-code has no settings block, so the settings could not be checked."]
    return [f"{key}: asked for {_norm(value)}, G-code has {_norm(found.get(key, 'missing'))}"
            for key, value in expected.items() if _norm(found.get(key, "missing")) != _norm(value)]
