"""Shared fixtures: a miniature profiles tree, a fake slicer and a test STL."""

import json
import os
import stat
import struct
import sys
from pathlib import Path

import pytest

REAL_PROFILES = os.environ.get("ORCA_TEST_PROFILES_DIR")


def _write(folder: Path, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{data['name']}.json").write_text(json.dumps(data))


@pytest.fixture
def profiles_dir(tmp_path: Path) -> Path:
    root = tmp_path / "profiles"
    ff, lib = root / "Flashforge", root / "OrcaFilamentLibrary"
    _write(ff / "machine", {"type": "machine", "name": "fdm_machine_common", "printable_height": "256", "nozzle_diameter": ["0.4"]})
    _write(ff / "machine", {"type": "machine", "name": "Flashforge Creator 5 0.4 nozzle", "inherits": "fdm_machine_common",
                            "instantiation": "true", "printer_model": "Flashforge Creator 5"})
    _write(ff / "process", {"type": "process", "name": "fdm_process_common", "wall_loops": "2", "line_width": "0.42",
                            "sparse_infill_density": "15%", "sparse_infill_pattern": "grid", "outer_wall_speed": "200",
                            "inner_wall_speed": "300", "enable_support": "0", "brim_type": "no_brim", "brim_width": "5",
                            "top_shell_layers": "5", "bottom_shell_layers": "3", "enable_prime_tower": "1"})
    for height in ("0.12", "0.20", "0.24"):
        _write(ff / "process", {"type": "process", "name": f"{height}mm Standard @FF C5", "inherits": "fdm_process_common",
                                "instantiation": "true", "layer_height": height.rstrip("0") if height != "0.20" else "0.2",
                                "compatible_printers": ["Flashforge Creator 5 0.4 nozzle"]})
    # The same parent name exists in both vendor folders with different contents, as in Flash Studio.
    _write(lib / "filament" / "base", {"type": "filament", "name": "fdm_filament_common", "filament_diameter": ["1.75"],
                                       "filament_density": ["1.24"]})
    _write(ff / "filament", {"type": "filament", "name": "fdm_filament_common", "filament_diameter": ["2.85"],
                             "filament_density": ["9.99"]})
    _write(lib / "filament", {"type": "filament", "name": "Generic PLA @System", "inherits": "fdm_filament_common",
                              "filament_type": ["PLA"], "nozzle_temperature": ["220"], "hot_plate_temp": ["60"],
                              "nozzle_temperature_initial_layer": ["220"], "hot_plate_temp_initial_layer": ["60"],
                              "textured_plate_temp": ["65"], "textured_plate_temp_initial_layer": ["65"],
                              "filament_max_volumetric_speed": ["18"]})
    for name, kind, temp in (("Flashforge PLA Basic", "PLA", "210"), ("Generic PLA", "PLA", "220"), ("Flashforge PETG Pro", "PETG", "255"),
                             ("Flashforge ABS Basic", "ABS", "270"), ("Flashforge TPU-95A", "TPU-95A", "230"),
                             ("Flashforge PLA Silk", "PLA", "230")):
        _write(ff / "filament", {"type": "filament", "name": f"{name} @FF C5", "inherits": "Generic PLA @System",
                                 "instantiation": "true", "filament_type": [kind], "nozzle_temperature": [temp],
                                 "compatible_printers": ["Flashforge Creator 5 0.4 nozzle"]})
    return root


@pytest.fixture
def real_profiles() -> Path:
    if not REAL_PROFILES or not Path(REAL_PROFILES, "Flashforge").is_dir():
        pytest.skip("Set ORCA_TEST_PROFILES_DIR to an OrcaSlicer resources/profiles folder")
    return Path(REAL_PROFILES)


def _stl(path: Path, triangles: list) -> Path:
    data = b"\0" * 80 + struct.pack("<I", len(triangles))
    for tri in triangles:
        data += struct.pack("<12fH", 0, 0, 0, *[c for v in tri for c in v], 0)
    path.write_bytes(data)
    return path


def box_triangles(sx: float, sy: float, sz: float, z0: float = 0.0) -> list:
    v = [(x, y, z) for z in (z0, z0 + sz) for y in (0, sy) for x in (0, sx)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return [t for a, b, c, d in quads for t in ((v[a], v[b], v[c]), (v[a], v[c], v[d]))]


@pytest.fixture
def cube_stl(tmp_path: Path) -> Path:
    return _stl(tmp_path / "cube.stl", box_triangles(20, 20, 10))


@pytest.fixture
def mushroom_stl(tmp_path: Path) -> Path:
    """A thin stem with a wide cap: tall for its base, with a large overhang."""
    stem = [[(x + 20, y + 20, z) for x, y, z in tri] for tri in box_triangles(5, 5, 40)]
    return _stl(tmp_path / "mushroom.stl", stem + box_triangles(45, 45, 5, z0=40))


FAKE_SLICER = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
if "--help" in args:
    print("FakeSlicer 9.9.9"); sys.exit(0)
out = Path(args[args.index("--outputdir") + 1])
settings = args[args.index("--load-settings") + 1].split(";")
process = json.loads(Path(settings[1]).read_text())
if process.get("wall_loops") == "99":
    sys.stderr.write("simulated failure\n"); sys.exit(3)
out.mkdir(parents=True, exist_ok=True)
body = ("; HEADER_BLOCK_START\n; total estimated time: 1h 23m 45s\n; total layer number: 180\n; HEADER_BLOCK_END\nG1 X0\n"
        "; filament used [mm] = 5000.5\n; filament used [g] = 15.25\n")
if process.get("wall_loops") == "98":          # killed part-way through writing
    (out / "plate_1.gcode").write_text(body[:60]); sys.exit(-11)
(out / "plate_1.gcode").write_text(body + ";end_gcode\n; EXECUTABLE_BLOCK_END\n")
(out / "argv.json").write_text(json.dumps(args))
if process.get("wall_loops") == "97":          # finishes the file, then crashes on the way out
    import os, signal
    os.kill(os.getpid(), signal.SIGSEGV)
'''


@pytest.fixture
def fake_env(tmp_path: Path, profiles_dir: Path, monkeypatch) -> Path:
    slicer = tmp_path / "fake-slicer"
    slicer.write_text(FAKE_SLICER.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    slicer.chmod(slicer.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ORCA_SLICER_PATH", str(slicer))
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(profiles_dir))
    monkeypatch.setenv("C5_OUTPUT_DIR", str(tmp_path / "out"))
    for key in ("C5_PRINTER_IP", "C5_PRINTER_SERIAL", "C5_PRINTER_CHECK_CODE"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path
