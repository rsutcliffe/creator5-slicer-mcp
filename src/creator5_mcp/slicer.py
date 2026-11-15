"""Run the OrcaSlicer command line and read back what it produced."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from . import profiles
from .config import Settings


class SliceError(RuntimeError):
    pass


_TIME = re.compile(r";\s*(?:total estimated time|estimated printing time \(normal mode\))\s*[:=]\s*([0-9dhms ]+)", re.I)
_FIELDS = {
    "filament_used_g": re.compile(r";\s*(?:total )?filament used \[g\]\s*[:=]\s*([0-9.]+)", re.I),
    "filament_used_mm": re.compile(r";\s*(?:total )?filament used \[mm\]\s*[:=]\s*([0-9.]+)", re.I),
    "filament_used_cm3": re.compile(r";\s*(?:total )?filament used \[cm3\]\s*[:=]\s*([0-9.]+)", re.I),
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
            out[key] = float(match.group(1)) if "." in match.group(1) else int(match.group(1))
    return out


def version(settings: Settings) -> str:
    try:
        result = subprocess.run([str(settings.require_slicer()), "--help"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable ({exc})"
    first = (result.stdout or result.stderr).strip().splitlines()
    return first[0] if first else "unknown"


def slice_model(
    settings: Settings,
    model: Path,
    plan: dict,
    extra_process: dict | None = None,
    extra_filament: dict | None = None,
    job_name: str | None = None,
    auto_orient: bool = False,
    copies: int = 1,
    timeout: int = 900,
) -> dict:
    """Write flattened presets for `plan`, slice `model`, and summarise the result."""
    binary = settings.require_slicer()
    profiles_dir = settings.require_profiles()
    if not model.is_file():
        raise SliceError(f"Model not found: {model}")
    if copies < 1:
        raise SliceError("copies must be at least 1")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", job_name or model.stem).strip("_") or "print"
    job_dir = settings.output_dir / f"{stamp}_{name}"
    preset_dir = job_dir / "presets"

    # Recommended settings go on first; the caller's own overrides go on last and
    # are the only ones checked for unfamiliar keys.
    machine = profiles.flatten(profiles_dir, "machine", plan["machine_preset"])
    process, _ = profiles.apply_overrides(
        profiles.flatten(profiles_dir, "process", plan["process_preset"]), plan["process_overrides"])
    process, unknown = profiles.apply_overrides(process, extra_process)
    filament, _ = profiles.apply_overrides(
        profiles.flatten(profiles_dir, "filament", plan["filament_preset"]), plan["filament_overrides"])
    filament, missing = profiles.apply_overrides(filament, extra_filament)
    unknown += missing

    machine_path = profiles.write_preset(machine, preset_dir / "machine.json")
    process_path = profiles.write_preset(process, preset_dir / "process.json")
    filament_path = profiles.write_preset(filament, preset_dir / "filament.json")

    project = job_dir / f"{name}.3mf"
    args = [
        str(binary),
        "--load-settings", f"{machine_path};{process_path}",
        "--load-filaments", str(filament_path),
        "--orient", "1" if auto_orient else "0",
        "--arrange", "1",
        "--slice", "0",
        "--outputdir", str(job_dir),
        "--export-3mf", project.name,
    ]
    args += [str(model)] * copies
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SliceError(f"Slicer timed out after {timeout} s") from exc
    (job_dir / "slicer.log").write_text(
        "$ " + " ".join(args) + f"\n\nexit code {result.returncode}\n\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
        encoding="utf-8")

    gcodes = sorted(job_dir.rglob("*.gcode"))
    if not gcodes:
        tail = (result.stderr or result.stdout).strip()[-1500:]
        raise SliceError(f"Slicer exited with code {result.returncode} and wrote no G-code. Log: {job_dir / 'slicer.log'}\n{tail}")

    return {
        "job_dir": str(job_dir),
        "gcode": str(gcodes[0]),
        "project_3mf": str(project) if project.is_file() else None,
        "exit_code": result.returncode,
        "summary": parse_gcode_summary(gcodes[0]),
        "presets_written": {"machine": str(machine_path), "process": str(process_path), "filament": str(filament_path)},
        "settings_not_in_base_preset": unknown,
        "log": str(job_dir / "slicer.log"),
    }
