"""Slice from the terminal, with no quotation marks needed.

    python -m creator5_mcp.cli MODEL --filament ender-pla --strength functional --copies 2 --open
"""

from __future__ import annotations

import argparse
import sys

import os
import re
import shutil
from pathlib import Path

from . import config, server


def _usb_volumes() -> list[Path]:
    """Removable drives currently plugged in (macOS and Linux)."""
    roots = [Path("/Volumes"), Path("/media") / os.environ.get("USER", ""), Path("/run/media") / os.environ.get("USER", "")]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            try:
                if entry.is_dir() and os.path.ismount(entry) and not entry.samefile("/") and os.access(entry, os.W_OK):
                    found.append(entry)
            except OSError:
                continue
    return found


def _copy(gcode: str, folder: Path, name: str) -> Path:
    target = folder / (re.sub(r"[^A-Za-z0-9._-]+", "_", name) + ".gcode")
    shutil.copyfile(gcode, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creator5_mcp.cli", description="Slice a model for the Flashforge Creator 5.")
    parser.add_argument("model", nargs="?", help="path to an STL, 3MF or OBJ file")
    parser.add_argument("--filament", nargs="+", default=["PLA"], help="e.g. PLA, PETG, ender-pla, silk-pla, TPU")
    parser.add_argument("--quality", default="standard", choices=["draft", "standard", "fine"])
    parser.add_argument("--strength", default="standard", choices=["decorative", "standard", "functional", "structural"])
    parser.add_argument("--nozzle", default="0.4", choices=["0.25", "0.4", "0.6", "0.8"])
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--supports", default="auto", choices=["auto", "on", "off"])
    parser.add_argument("--brim", default="auto", choices=["auto", "on", "off"])
    parser.add_argument("--nozzle-temp", type=int, default=0)
    parser.add_argument("--bed-temp", type=int, default=0)
    parser.add_argument("--max-speed", type=int, default=0)
    parser.add_argument("--auto-orient", action="store_true")
    parser.add_argument("--smooth-top", action="store_true", help="iron flat top surfaces for a smoother finish")
    parser.add_argument("--precise-fit", action="store_true", help="tighter outer dimensions for mating parts")
    parser.add_argument("--open", action="store_true", help="show the result in the slicer's preview when done")
    parser.add_argument("--usb", action="store_true", help="copy the G-code to the USB stick that is plugged in")
    parser.add_argument("--copy-to", metavar="FOLDER", help="copy the G-code to this folder")
    parser.add_argument("--send", action="store_true", help="upload the G-code to the printer over the network")
    parser.add_argument("--print", dest="start", action="store_true", help="upload and start printing")
    parser.add_argument("--save-printer", nargs=3, metavar=("IP", "SERIAL", "CHECKCODE"),
                        help="remember the printer's network details, then exit")
    parser.add_argument("--printer-status", action="store_true", help="show the printer's state, then exit")
    args = parser.parse_args(argv)

    if args.save_printer:
        path = config.save_printer(*args.save_printer)
        print(f"Printer details saved to {path}")
        status = server.printer_status()
        print("Printer answered: connection works." if status["ok"] else "Could not reach the printer yet: " + status["error"])
        return 0 if status["ok"] else 1
    if args.printer_status:
        status = server.printer_status()
        print(status["status"] if status["ok"] else "FAILED: " + status["error"])
        return 0 if status["ok"] else 1
    if not args.model:
        parser.error("give the path to a model file")

    result = server.slice_model(
        args.model, " ".join(args.filament).replace("-", " "), args.quality, args.strength, args.nozzle,
        args.supports, args.brim, copies=args.copies, auto_orient=args.auto_orient,
        smooth_top=args.smooth_top, precise_fit=args.precise_fit,
        nozzle_temp=args.nozzle_temp, bed_temp=args.bed_temp, max_speed=args.max_speed)
    if not result["ok"]:
        print("FAILED: " + result["error"])
        return 1
    plan, summary = result["plan"], result["summary"]
    print(f"Sliced {args.copies} x {args.model}")
    print(f"  Material   {plan['material']}  ({plan['filament_preset']})")
    print(f"  Process    {plan['process_preset']}")
    for key in ("wall_loops", "sparse_infill_density", "sparse_infill_pattern", "enable_support", "brim_type", "ironing_type"):
        if key in plan["process_overrides"]:
            print(f"  {key:<22} {plan['process_overrides'][key]}")
    for key, value in plan["filament_overrides"].items():
        if not key.endswith("_initial_layer") and not key.startswith("textured"):
            print(f"  {key:<22} {value}")
    print(f"  Print time {summary.get('print_time', '?')}   Filament {summary.get('filament_used_g', '?')} g")
    for warning in plan["warnings"] + result["notes"]:
        print("  NOTE: " + warning)
    if result.get("project_3mf"):
        print("  Project    " + result["project_3mf"])
    print("  G-code     " + result["gcode"])
    name = Path(args.model).stem + (f"_x{args.copies}" if args.copies > 1 else "")
    failed = False
    if args.open:
        target = result.get("project_3mf") or result["gcode"]
        opened = server.open_in_slicer(target)
        if not opened["ok"]:
            print("  Could not open the slicer: " + opened["error"])
        elif result.get("project_3mf"):
            print("  Opened as a project in the slicer: press Slice, then Print.")
        else:
            print("  Shown in the slicer's preview (view only).")
    if args.copy_to:
        print(f"  Copied to  {_copy(result['gcode'], Path(args.copy_to).expanduser(), name)}")
    if args.usb:
        drives = _usb_volumes()
        if len(drives) == 1:
            print(f"  Copied to USB: {_copy(result['gcode'], drives[0], name)}  (eject it before unplugging)")
        elif not drives:
            print("  No USB stick found. Plug one in and run the command again.")
            failed = True
        else:
            print("  More than one drive found; pick one with --copy-to: " + ", ".join(str(d) for d in drives))
            failed = True
    if args.send or args.start:
        sent = server.send_to_printer(result["gcode"], start_print=args.start)
        if sent["ok"]:
            print("  Sent to the printer and started." if args.start else "  Uploaded to the printer. Start it from the printer's screen.")
        else:
            print("  Could not send to the printer: " + sent["error"])
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
