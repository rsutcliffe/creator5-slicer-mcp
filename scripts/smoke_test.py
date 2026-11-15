#!/usr/bin/env python3
"""Slice a 20 mm test cube with the real slicer to confirm the whole chain works.

Run this once after installing:  python scripts/smoke_test.py
It writes into the normal output folder and prints what the slicer produced.
"""

import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from creator5_mcp import server  # noqa: E402


def cube(path: Path, size: float = 20.0) -> Path:
    v = [(x, y, z) for z in (0, size) for y in (0, size) for x in (0, size)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = [t for a, b, c, d in quads for t in ((v[a], v[b], v[c]), (v[a], v[c], v[d]))]
    data = b"\0" * 80 + struct.pack("<I", len(tris))
    for tri in tris:
        data += struct.pack("<12fH", 0, 0, 0, *[c for p in tri for c in p], 0)
    path.write_bytes(data)
    return path


def main() -> int:
    setup = server.check_setup()
    print(json.dumps(setup, indent=2))
    if not setup["ok"]:
        print("\nSetup is incomplete; fix the item above and run again.")
        return 1
    model = cube(Path(tempfile.mkdtemp()) / "smoke_cube.stl")
    result = server.slice_model(str(model), "PLA", job_name="smoke_test")
    if not result["ok"]:
        print("\nSLICE FAILED\n" + result["error"])
        return 1
    print("\nSLICE OK (file only; nothing was sent to the printer)")
    print(json.dumps({k: result[k] for k in ("project_3mf", "gcode", "gcode_complete", "summary", "exit_code", "notes", "log",
                                             "settings_checked_in_gcode", "settings_mismatched")}, indent=2))
    if result["settings_mismatched"]:
        print("\nSETTINGS DID NOT REACH THE G-CODE; see settings_mismatched above.")
        return 1
    if not result["summary"]:
        print("\nNote: the G-code was written but its time and filament comments were not recognised. "
              "Please open an issue with the first 40 lines of the G-code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
