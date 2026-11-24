"""Will it print? The check is text-to-cad's `cad` command (MIT, github.com/earthtojake/text-to-cad),
an optional outside tool like the STEP converter: walls, overhangs, bridges, knife edges, holes and
clearances against published design guides, and the ranking of the ways the part could lie."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from . import model
from .model import BED_X, BED_Y, BED_Z

KINDS = {".step": "step", ".stp": "step", ".stl": "stl", ".3mf": "threemf"}
# The first of these words in the server's material label (Silk PLA, PAHT-CF) gives the name `cad --material` takes.
# ASA is judged as ABS: both warp the same way, and cad has no ASA.
MATERIALS = {"pla": "pla", "petg": "petg", "pet": "petg", "abs": "abs", "asa": "abs", "tpu": "tpu",
             "pa": "nylon", "paht": "nylon", "ppa": "nylon", "pps": "pps", "hips": "ps"}
NOT_JUDGED = ("families_not_measured", "mesh_at_limit", "measurement_failed", "rule_partly_judged", "rules_none_evaluated")
SUPPORT_SAVED_MM3 = 1000.0   # another way up must save this much support (about 1.2 g), and at least half, to be worth saying
MESH_SECONDS = 30           # how long a mesh's check and its ranking of ways up may take together
EXACT_SECONDS = 60          # how long a STEP's exact check may take before a mesh of it is checked instead
INSTALL = "Install it with: uv tool install cad (or re-run scripts/setup.sh)."


def binary() -> str | None:
    """The cad command. Claude Desktop starts the server without the shell's PATH, so uv's folder is tried too."""
    found = shutil.which("cad")
    local = Path.home() / ".local/bin/cad"
    return found or (str(local) if local.is_file() else None)


def version() -> str | None:
    cad = binary()
    if not cad:
        return None
    try:
        return subprocess.run([cad, "--version"], capture_output=True, text=True, timeout=20).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _run(args: list[str], timeout: int) -> tuple[dict | None, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"The check took longer than {timeout:.0f} seconds and was stopped."
    except OSError as exc:
        return None, str(exc)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1]), ""
    except (ValueError, IndexError):
        return None, (done.stderr.strip().splitlines() or ["cad gave no result"])[-1]


def _say(found: list[dict]) -> str:
    """One rule's findings as one sentence: what, how many, the fix, where, and whose limit it is."""
    first = found[0]
    text = first["message"]
    if len(found) > 1:
        text += f" ({len(found)} places)"
    if first.get("hint"):
        text += f". Fix: {first['hint']}"
    if first.get("location") and first["location"] not in ("mesh", "rules"):
        text += f". At x, y, z = {first['location']} mm" if first["location"][:1].isdigit() or first["location"][:1] == "-" else f". At {first['location']}"
    if first.get("source"):
        text += f". Limit from: {first['source']}"
    return text[:1].upper() + text[1:] + "."


def check(path: Path, nozzle: float = 0.4, layer_height: float = 0.2, material: str | None = None, timeout: int = MESH_SECONDS) -> dict:
    """Check the model at path for FDM printing on the Creator 5, as it stands in the file.

    Returns {"checked": False, "reason": ...} when nothing was judged: never a pass. Otherwise
    status (ok or failed), errors, warnings and notes in plain words, not_judged (what this file
    could not be measured for), exact (a STEP is measured exactly, a mesh by sampling), and
    orientation: how the part stands now against the best of the ways it could lie.
    """
    cad = binary()
    kind = KINDS.get(path.suffix.lower())
    if not kind:
        return {"checked": False, "reason": f"{path.suffix} files are not checked for printing. Use STL, 3MF or STEP."}
    if not cad:
        return {"checked": False, "reason": "The print check needs the cad command. " + INSTALL}
    options = ["--process", "printing", "--technology", "fdm", "--build-volume", f"{BED_X:g}x{BED_Y:g}x{BED_Z:g}",
               "--nozzle", f"{nozzle:g}", "--layer-height", f"{layer_height:g}", "--json"]
    stuff = next((MATERIALS[word] for word in re.findall(r"[a-z]+", (material or "").lower()) if word in MATERIALS), None)
    # cad's checks run in its server. One left running by someone else is used and left; ours is stopped after.
    try:
        ours = "started the cad server" in subprocess.run([cad, "server", "start"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"checked": False, "reason": f"The cad server did not start: {exc}"}

    def measure(kind: str, source: Path, seconds: float, rank: bool = True) -> tuple[dict | None, str, dict | None]:
        base = [cad, kind, "dfm", str(source), *options]
        until = time.time() + seconds          # one allowance for both runs: a plate of many bodies can take minutes
        result, why = _run(base + (["--material", stuff] if stuff else []), seconds)
        left = until - time.time()
        judged = bool(result and "findings" in result)
        return result, why, _run(base + ["--candidates"], left)[0] if rank and judged and left > 1 else None

    def by_part(parts: list[dict]) -> tuple[dict | None, str, None]:
        """A file of several parts is checked a part at a time, largest first, each alike part once. Lettering made of
        many small bodies can take minutes by itself; what the allowance does not reach is named as not judged."""
        until, seen, findings, skipped, failed = time.time() + timeout, set(), [], [], False
        with tempfile.TemporaryDirectory(prefix="creator5_check_") as folder:
            for number, part in enumerate(sorted(parts, key=lambda p: -float(np.ptp(p["tris"].reshape(-1, 3), axis=0).prod()))):
                alike = (len(part["tris"]), tuple(np.round(np.ptp(part["tris"].reshape(-1, 3), axis=0), 2)))
                if alike in seen:
                    continue
                seen.add(alike)
                left = until - time.time()
                found, why = (measure("stl", model.write_stl(Path(folder) / f"part{number}.stl", part["tris"]), left, rank=False)[:2]
                              if left > 2 else (None, "the time allowed ran out"))
                if not (found and "findings" in found):
                    skipped.append({"severity": "info", "code": "measurement_failed", "location": "rules", "hint": None, "source": None,
                                    "part": part["name"], "message": f"'{part['name']}' was not checked: {(why or 'no result').rstrip('.')}"})
                    continue
                failed = failed or found["status"] == "failed"
                findings += [{**f, "part": part["name"], "message": f"'{part['name']}': {f['message']}"} for f in found["findings"]]
        if not findings:
            return None, skipped[0]["message"] if skipped else "nothing to check", None
        return {"status": "failed" if failed else "ok", "findings": findings + skipped, "summary": f"Checked one part at a time: {len(seen)} different parts"}, "", None

    unfinished = None
    try:
        several = [part for obj in model.load_objects(path)["objects"] for part in obj["parts"]] if kind == "threemf" else []
        if len(several) > 1:
            result, why, ranked = by_part(several)
        else:
            result, why, ranked = measure(kind, path, min(timeout, EXACT_SECONDS) if kind == "step" else timeout)
        if kind == "step" and not (result and "findings" in result):
            # Exact measuring of many curved faces can take minutes. The part is still checked, as a mesh, and the result says so.
            unfinished = why or (result or {}).get("error") or "it gave no result"
            if ours:   # drop the unfinished exact check
                subprocess.run([cad, "server", "stop"], capture_output=True, timeout=60)
                subprocess.run([cad, "server", "start"], capture_output=True, timeout=60)
            with tempfile.TemporaryDirectory(prefix="creator5_check_") as folder:
                mesh = Path(folder) / f"{path.stem}.stl"
                subprocess.run([cad, "step", "export", str(path), str(mesh)], capture_output=True, timeout=timeout)
                if mesh.is_file():
                    result, why, ranked = measure("stl", mesh, timeout)
    except (OSError, subprocess.TimeoutExpired, model.ModelError) as exc:
        return {"checked": False, "reason": f"The print check did not run: {exc}"}
    finally:
        if ours:
            subprocess.run([cad, "server", "stop"], capture_output=True, timeout=60)
    if not result or "findings" not in result:
        return {"checked": False, "reason": why or (result or {}).get("error") or "cad gave no result"}
    by_code: dict[tuple, list[dict]] = {}
    for finding in result["findings"]:
        by_code.setdefault((finding["severity"], finding["code"], finding.get("part")), []).append(finding)
    out = {"checked": result["status"] != "not_checked", "status": result["status"], "exact": kind == "step" and not unfinished,
           "summary": result.get("summary", ""), "errors": [], "warnings": [], "notes": [], "not_judged": [], "orientation": None}
    for (severity, code, _), found in by_code.items():
        if code == "rules_general":
            continue
        if code in NOT_JUDGED or code == result.get("reason"):
            out["not_judged"].append(_say(found))
        else:
            out[{"error": "errors", "warning": "warnings"}.get(severity, "notes")].append(_say(found))
    if not out["checked"]:
        out["reason"] = result.get("summary") or result.get("reason")
    if unfinished:
        out["not_judged"].append(f"The exact check of the STEP did not finish ({unfinished.rstrip('.')}), so a mesh of it was checked instead.")
    if not out["exact"]:
        out["not_judged"].append("This is a mesh, measured by sampling: a thin place smaller than the sample spacing can be missed. "
                                 "The part's STEP file is measured exactly.")
    ways = ((ranked or {}).get("measurements") or {}).get("orientations", {}).get("candidates") or []
    now = next((w for w in ways if w["orientation"] == "current_plus_z"), None)
    best = min(ways, key=lambda w: w["rank"], default=None)
    if now and best:
        keep = ("orientation", "rotate", "support_volume_mm3", "overhang_area_mm2", "build_height_mm", "bed_contact_mm2")
        saved = now["support_volume_mm3"] - best["support_volume_mm3"]
        out["orientation"] = {"as_it_stands": {k: now[k] for k in keep}, "best": {k: best[k] for k in keep},
                              "best_is_as_it_stands": best is now}
        if best is not now and saved >= SUPPORT_SAVED_MM3 and best["support_volume_mm3"] <= now["support_volume_mm3"] / 2:
            out["warnings"].append(
                f"The part would need about {saved / 1000:.1f} cm³ less support another way up: {now['support_volume_mm3'] / 1000:.1f} cm³ "
                f"as it stands, {best['support_volume_mm3'] / 1000:.1f} cm³ turned by {best['rotate'].replace('bd.Rot', 'x, y, z degrees ')}. "
                "Pass auto_orient to let the slicer turn it, unless it is turned this way for strength.")
    return out
