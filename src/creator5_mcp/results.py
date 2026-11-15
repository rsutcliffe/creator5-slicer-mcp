"""What happened when a project was printed, kept so settings can be corrected from real prints.

Each record holds the settings the project carried, the outcome, and, when the owner saved the
project from Flash Studio after editing it, what he changed by hand.
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

from . import model

FILE_NAME = "print_results.json"
OUTCOMES = ("good", "acceptable", "failed")
_NOISE = {"name", "from", "version", "different_settings_to_system", "inherits_group", "printer_settings_id",
          "filament_colour", "default_filament_colour", "thumbnails", "print_host", "printhost_apikey"}


class ResultError(ValueError):
    pass


def _plain(value):
    """A setting in a form that compares equal however a slicer wrote it.

    Flash Studio pads every per-toolhead list to the machine's four toolheads when it saves and
    rewrites numbers ("0.50" becomes "0.5"), so a list is reduced to its distinct values.
    """
    def one(text):
        text = str(text).strip()
        try:
            return f"{float(text.rstrip('%')):g}" + ("%" if text.endswith("%") else "")
        except ValueError:
            return text
    if isinstance(value, list):
        distinct = sorted({one(v) for v in value})
        return distinct[0] if len(distinct) == 1 else distinct or ""
    return one(value)


def _names(project: Path) -> list[str]:
    try:
        return sorted({obj["name"] for obj in model.load_objects(project)["objects"]})
    except model.ModelError:
        return []


def _read(project: Path) -> tuple[dict, int]:
    """A project's settings and how many parts sit on its plate."""
    try:
        with zipfile.ZipFile(project) as archive:
            settings = json.loads(archive.read("Metadata/project_settings.config"))
            root = next((n for n in archive.namelist() if n.endswith("3dmodel.model") and n.count("/") == 1), None)
            items = archive.read(root).decode("utf-8", "ignore").count("<item ") if root else 0
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ResultError(f"{project.name} is not a slicer project this server can read: {exc}") from exc
    return settings, items


def summary(project: Path) -> dict:
    """The presets and every value that differs from them, as the project records it."""
    settings, items = _read(project)
    changed = [key for group in settings.get("different_settings_to_system", []) for key in group.split(";") if key]
    return {"process_preset": settings.get("print_settings_id"), "filament_presets": _plain(settings.get("filament_settings_id")),
            "objects": _names(project), "parts_on_plate": items,
            "changed": {key: _plain(settings.get(key)) for key in sorted(set(changed))}}


def differences(written: Path, printed: Path) -> dict:
    """What differs between the project this server wrote and the one saved from the slicer.

    Only settings both files hold are compared, so defaults one slicer writes and the other
    leaves out do not show up as changes.
    """
    ours, our_items = _read(written)
    theirs, their_items = _read(printed)
    changed = {key: {"written": _plain(ours[key]), "printed": _plain(theirs[key])}
               for key in sorted(ours.keys() & theirs.keys() - _NOISE) if _plain(ours[key]) != _plain(theirs[key])}
    out: dict = {"settings": changed}
    if _names(written) != _names(printed):
        out["objects"] = {"written": _names(written), "printed": _names(printed)}
    if our_items != their_items:
        out["parts_on_plate"] = {"written": our_items, "printed": their_items}
    return out


def load(output_dir: Path) -> list[dict]:
    try:
        data = json.loads((output_dir / FILE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record(output_dir: Path, project: Path, outcome: str, notes: str = "", printed_project: Path | None = None) -> dict:
    """Add one print's result to the log and return the record."""
    if outcome not in OUTCOMES:
        raise ResultError(f"outcome must be one of {', '.join(OUTCOMES)}")
    entry = {"date": time.strftime("%Y-%m-%d"), "project": str(project), "outcome": outcome, "notes": notes,
             **summary(project)}
    if printed_project is not None:
        entry["changed_by_hand"] = differences(project, printed_project)
    results = load(output_dir)
    results.append(entry)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / FILE_NAME).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return entry
