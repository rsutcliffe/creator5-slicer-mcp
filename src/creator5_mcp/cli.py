"""Slice from the terminal, with no quotation marks needed.

    python -m creator5_mcp.cli MODEL --filament ender-pla --strength functional --copies 2 --open
    python -m creator5_mcp.cli MODEL --filament black-pla --also white-pla --also gold-silk-pla --open
    python -m creator5_mcp.cli --save-spool black-pla --filament ender-pla --nozzle-temp 215 --bed-temp 60 --max-speed 80 --colour black
"""

from __future__ import annotations

import argparse
import sys

from . import recommend, server


def _words(parts: list[str]) -> str:
    return " ".join(parts).replace("-", " ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creator5_mcp.cli", description="Slice a model for the Flashforge Creator 5.")
    parser.add_argument("model", nargs="?", help="path to an STL, 3MF, OBJ or STEP file")
    parser.add_argument("--filament", nargs="+", default=["PLA"], help="toolhead 1: e.g. PLA, PETG, ender-pla, silk-pla, or a saved spool")
    parser.add_argument("--also", nargs="+", action="append", default=[], metavar="FILAMENT",
                        help="the filament in the next toolhead; repeat for toolheads 2, 3 and 4")
    parser.add_argument("--quality", default="standard", choices=list(recommend.QUALITY))
    parser.add_argument("--strength", default="standard", choices=list(recommend.STRENGTH))
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
    parser.add_argument("--open", action="store_true", help="open the project in the slicer when done")
    parser.add_argument("--save-spool", nargs="+", metavar="NAME",
                        help="remember a spool under this name, with --filament and the label figures, then exit")
    parser.add_argument("--colour", default="", help="with --save-spool: a colour word or hex value")
    parser.add_argument("--max-flow", type=float, default=0, help="with --save-spool: a proven flow limit in mm3/s")
    parser.add_argument("--record-result", metavar="PROJECT", help="record how this project printed, then exit")
    parser.add_argument("--outcome", default="good", choices=["good", "acceptable", "failed"], help="with --record-result")
    parser.add_argument("--notes", nargs="+", default=[], help="with --record-result: what happened, in plain words")
    parser.add_argument("--printed-project", metavar="PROJECT",
                        help="with --record-result: the project as saved from the slicer, to record what was changed by hand")
    args = parser.parse_args(argv)

    if args.save_spool:
        saved = server.save_spool(_words(args.save_spool), _words(args.filament), args.nozzle_temp, args.bed_temp,
                                  args.max_speed, args.colour, args.max_flow)
        print(f"Saved spool: {saved['spool']}" if saved["ok"] else "FAILED: " + saved["error"])
        return 0 if saved["ok"] else 1
    if args.record_result:
        done = server.record_print_result(args.record_result, args.outcome, " ".join(args.notes), args.printed_project)
        print(f"Recorded: {done['result']['outcome']} ({done['results_recorded']} results so far)" if done["ok"] else "FAILED: " + done["error"])
        return 0 if done["ok"] else 1
    if not args.model:
        parser.error("give the path to a model file")

    result = server.slice_model(
        args.model, _words(args.filament), args.quality, args.strength, args.nozzle,
        args.supports, args.brim, copies=args.copies, auto_orient=args.auto_orient,
        smooth_top=args.smooth_top, precise_fit=args.precise_fit,
        nozzle_temp=args.nozzle_temp, bed_temp=args.bed_temp, max_speed=args.max_speed,
        other_filaments=[_words(f) for f in args.also] or None)
    if not result["ok"]:
        print("FAILED: " + result["error"])
        return 1
    plan, summary = result["plan"], result["summary"]
    print(f"Sliced {args.copies} x {', '.join(result['objects'])}")
    for i, slot in enumerate(plan["filaments"]):
        print(f"  Toolhead {i + 1} {slot['material']}  ({slot['preset']})  {slot['colour']}")
    print(f"  Process    {plan['process_preset']}")
    for key in ("wall_loops", "sparse_infill_density", "sparse_infill_pattern", "enable_support", "brim_type", "ironing_type"):
        if key in plan["process_overrides"]:
            print(f"  {key:<22} {plan['process_overrides'][key]}")
    for key, value in plan["filament_overrides"].items():
        if not key.endswith("_initial_layer") and not key.startswith("textured"):
            print(f"  {key:<22} {value}")
    print(f"  Print time {summary.get('print_time', '?')}   Filament {summary.get('filament_used_g', '?')} g")
    print(f"  Checked    {result['settings_checked_in_gcode']} settings read back from the G-code")
    print(f"  Supports   {'on' if result['supports_on'] else 'off'}, {result['support_sections_in_gcode']} stretches in the G-code")
    for warning in plan["warnings"] + result["notes"]:
        print("  NOTE: " + warning)
    print("  Project    " + result["project_3mf"])
    if args.open:
        opened = server.open_in_slicer(result["project_3mf"])
        print("  Opened as a project in the slicer: press Slice, then Print." if opened["ok"]
              else "  Could not open the slicer: " + opened["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
