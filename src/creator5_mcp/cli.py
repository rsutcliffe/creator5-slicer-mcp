"""Slice from the terminal, with no quotation marks needed.

    python -m creator5_mcp.cli MODEL --filament ender-pla --strength functional --copies 2 --open
"""

from __future__ import annotations

import argparse
import sys

from . import server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creator5_mcp.cli", description="Slice a model for the Flashforge Creator 5.")
    parser.add_argument("model", help="path to an STL, 3MF, STEP or OBJ file")
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
    parser.add_argument("--open", action="store_true", help="open the result in the slicer when done")
    args = parser.parse_args(argv)

    result = server.slice_model(
        args.model, " ".join(args.filament).replace("-", " "), args.quality, args.strength, args.nozzle,
        args.supports, args.brim, copies=args.copies, auto_orient=args.auto_orient,
        nozzle_temp=args.nozzle_temp, bed_temp=args.bed_temp, max_speed=args.max_speed)
    if not result["ok"]:
        print("FAILED: " + result["error"])
        return 1
    plan, summary = result["plan"], result["summary"]
    print(f"Sliced {args.copies} x {args.model}")
    print(f"  Material   {plan['material']}  ({plan['filament_preset']})")
    print(f"  Process    {plan['process_preset']}")
    for key in ("wall_loops", "sparse_infill_density", "sparse_infill_pattern", "enable_support", "brim_type"):
        print(f"  {key:<22} {plan['process_overrides'][key]}")
    for key, value in plan["filament_overrides"].items():
        if not key.endswith("_initial_layer") and not key.startswith("textured"):
            print(f"  {key:<22} {value}")
    print(f"  Print time {summary.get('print_time', '?')}   Filament {summary.get('filament_used_g', '?')} g")
    for warning in plan["warnings"] + result["notes"]:
        print("  NOTE: " + warning)
    print("  G-code     " + result["gcode"])
    if args.open:
        opened = server.open_in_slicer(result["gcode"])
        print("  Opened in the slicer." if opened["ok"] else "  Could not open the slicer: " + opened["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
