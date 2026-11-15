"""Run the slicer's command line on a project file and read back what it produced."""

from __future__ import annotations

import re
import subprocess
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
