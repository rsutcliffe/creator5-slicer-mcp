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
    _write(ff / "machine", {"type": "machine", "name": "fdm_machine_common", "printable_height": "256", "nozzle_diameter": ["0.4"] * 4})
    _write(ff / "machine", {"type": "machine", "name": "Flashforge Creator 5 0.4 nozzle", "inherits": "fdm_machine_common",
                            "instantiation": "true", "printer_model": "Flashforge Creator 5",
                            "machine_pause_gcode": "M25"})
    _write(ff / "process", {"type": "process", "name": "fdm_process_common", "wall_loops": "2", "line_width": "0.42",
                            "sparse_infill_density": "15%", "sparse_infill_pattern": "grid", "outer_wall_speed": "200",
                            "inner_wall_speed": "300", "enable_support": "0", "brim_type": "no_brim", "brim_width": "5",
                            "top_shell_layers": "5", "bottom_shell_layers": "3", "enable_prime_tower": "1",
                            "support_top_z_distance": "0.2"})
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


def write_step_box(path: Path, sx: float, sy: float, sz: float) -> Path:
    """A minimal AP214 STEP file holding one solid box, as a CAD program would export it."""
    import itertools
    count = [0]; lines = []
    def e(text):
        count[0] += 1; lines.append(f"#{count[0]}={text};"); return f"#{count[0]}"
    pt = lambda p: e(f"CARTESIAN_POINT('',({p[0]:.4f},{p[1]:.4f},{p[2]:.4f}))")
    dr = lambda d: e(f"DIRECTION('',({d[0]:.1f},{d[1]:.1f},{d[2]:.1f}))")
    corners = {c: (c[0] * sx, c[1] * sy, c[2] * sz) for c in itertools.product((0, 1), repeat=3)}
    vert = {c: e(f"VERTEX_POINT('',{pt(p)})") for c, p in corners.items()}
    edges = {}
    def edge(a, b):
        key = (a, b) if a < b else (b, a)
        if key not in edges:
            d = tuple(float(key[1][i] - key[0][i]) for i in range(3))
            vector = e(f"VECTOR('',{dr(d)},1.0)")
            line = e(f"LINE('',{pt(corners[key[0]])},{vector})")
            edges[key] = e(f"EDGE_CURVE('',{vert[key[0]]},{vert[key[1]]},{line},.T.)")
        return edges[key], ".T." if (a, b) == key else ".F."
    faces = []
    for axis, side in itertools.product(range(3), (0, 1)):
        u, v = [(1, 2), (2, 0), (0, 1)][axis]
        def c(i, j):
            t = [0, 0, 0]; t[axis] = side; t[u] = i; t[v] = j; return tuple(t)
        loop = [c(0, 0), c(1, 0), c(1, 1), c(0, 1)]
        if side == 0:
            loop.reverse()
        oriented = [e("ORIENTED_EDGE('',*,*,%s,%s)" % edge(loop[k], loop[(k + 1) % 4])) for k in range(4)]
        edge_loop = e(f"EDGE_LOOP('',({','.join(oriented)}))")
        bound = e(f"FACE_OUTER_BOUND('',{edge_loop},.T.)")
        normal = [0.0, 0.0, 0.0]; normal[axis] = 1.0 if side else -1.0
        ref = [0.0, 0.0, 0.0]; ref[u] = 1.0
        placement = e(f"AXIS2_PLACEMENT_3D('',{pt(corners[c(0, 0)])},{dr(normal)},{dr(ref)})")
        faces.append(e(f"ADVANCED_FACE('',({bound}),{e(f'PLANE({chr(39)}{chr(39)},{placement})')},.T.)"))
    shell = e(f"CLOSED_SHELL('',({','.join(faces)}))")
    solid = e(f"MANIFOLD_SOLID_BREP('box',{shell})")
    app = e("APPLICATION_CONTEXT('automotive design')")
    e(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,{app})")
    product = e(f"PRODUCT('box','box','',({e(f'PRODUCT_CONTEXT({chr(39)}{chr(39)},{app},{chr(39)}mechanical{chr(39)})')}))")
    formation = e(f"PRODUCT_DEFINITION_FORMATION('','',{product})")
    context = e(f"PRODUCT_DEFINITION_CONTEXT('part definition',{app},'design')")
    shape = e(f"PRODUCT_DEFINITION_SHAPE('','',{e(f'PRODUCT_DEFINITION({chr(39)}design{chr(39)},{chr(39)}{chr(39)},{formation},{context})')})")
    lu = e("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    au = e("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    su = e("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = e(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),{lu},'distance_accuracy_value','')")
    ctx = e(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc}))GLOBAL_UNIT_ASSIGNED_CONTEXT(({lu},{au},{su}))REPRESENTATION_CONTEXT('',''))")
    origin = e(f"AXIS2_PLACEMENT_3D('',{pt((0, 0, 0))},{dr((0, 0, 1))},{dr((1, 0, 0))})")
    e(f"SHAPE_DEFINITION_REPRESENTATION({shape},{e(f'ADVANCED_BREP_SHAPE_REPRESENTATION({chr(39)}{chr(39)},({origin},{solid}),{ctx})')})")
    path.write_text("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('box.step','2026-11-15T00:00:00',(''),(''),'','','');\n"
                    "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n" + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")
    return path


@pytest.fixture
def cube_stl(tmp_path: Path) -> Path:
    return _stl(tmp_path / "cube.stl", box_triangles(20, 20, 10))


def make_3mf(path: Path, triangles: list, settings: dict | None = None, offset=(100.0, 100.0, 0.0)) -> Path:
    """A 3MF laid out as Bambu Studio and OrcaSlicer write it: the mesh in a separate object file,
    a modifier part that must be ignored, a second plate, and optional project settings."""
    import zipfile
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    prod = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
    verts = [v for tri in triangles for v in tri]
    def mesh(oid, tris_offset=0.0):
        vx = "".join(f'<vertex x="{x + tris_offset}" y="{y}" z="{z}"/>' for x, y, z in verts)
        tx = "".join(f'<triangle v1="{3*i}" v2="{3*i+1}" v3="{3*i+2}"/>' for i in range(len(triangles)))
        return f'<object id="{oid}" type="model"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>'
    sub = f'<model unit="millimeter" xmlns="{core}"><resources>{mesh(1)}{mesh(2, 500.0)}</resources></model>'
    root = (f'<model unit="millimeter" xmlns="{core}" xmlns:p="{prod}"><resources>'
            f'<object id="3" type="model"><components>'
            f'<component p:path="/3D/Objects/part.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            f'<component p:path="/3D/Objects/part.model" objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            f'</components></object>{mesh(4)}</resources><build>'
            f'<item objectid="3" transform="1 0 0 0 1 0 0 0 1 {offset[0]} {offset[1]} {offset[2]}" printable="1"/>'
            f'<item objectid="4" transform="1 0 0 0 1 0 0 0 1 400 0 0" printable="1"/></build></model>')
    cfg = ('<config><object id="3"><metadata key="name" value="holder v3.stl_1"/><part id="1" subtype="normal_part"/><part id="2" subtype="modifier_part"/></object>'
           '<object id="4"><part id="1" subtype="normal_part"/></object>'
           '<plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="3"/></model_instance></plate>'
           '<plate><metadata key="plater_id" value="2"/><model_instance><metadata key="object_id" value="4"/></model_instance></plate></config>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", root)
        z.writestr("3D/Objects/part.model", sub)
        z.writestr("Metadata/model_settings.config", cfg)
        if settings is not None:
            z.writestr("Metadata/project_settings.config", json.dumps(settings))
    return path


@pytest.fixture
def colour_3mf(tmp_path: Path) -> Path:
    """A plain 3MF as a CAD tool writes it: colours as base materials, two objects, one of two parts.
    Figure = a black body (T1) with a white cap (T2); Plinth = gold (T3), painted on one face."""
    import zipfile
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    def mesh(oid, name, pindex, triangles, paint=False):
        verts = [v for tri in triangles for v in tri]
        vx = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
        tx = "".join(f'<triangle v1="{3*i}" v2="{3*i+1}" v3="{3*i+2}"' + (' paint_color="8"' if paint and i == 0 else "") + "/>"
                     for i in range(len(triangles)))
        return f'<object id="{oid}" type="model" name="{name}" pid="1" pindex="{pindex}"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>'
    root = (f'<model unit="millimeter" xmlns="{core}"><resources>'
            '<basematerials id="1"><base name="T1 Black" displaycolor="#191919FF"/><base name="T2 White" displaycolor="#F0F0EBFF"/>'
            '<base name="T3 Gold" displaycolor="#C89B3CFF"/></basematerials>'
            + mesh(2, "T1 Black", 0, box_triangles(20, 20, 10)) + mesh(3, "T2 White", 1, box_triangles(20, 20, 5, z0=10))
            + mesh(4, "T3 Gold", 2, box_triangles(30, 30, 4), paint=True)
            + '<object id="5" type="model" name="Figure"><components><component objectid="2"/><component objectid="3"/></components></object>'
            '<object id="6" type="model" name="Plinth"><components><component objectid="4"/></components></object>'
            '</resources><build><item objectid="5"/><item objectid="6" transform="1 0 0 0 1 0 0 0 1 40 0 0"/></build></model>')
    path = tmp_path / "figure.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", root)
    return path


@pytest.fixture
def labelled_3mf(tmp_path: Path) -> Path:
    """A CAD export: a body and an inlay as two build items, toolheads named only in part numbers."""
    import zipfile
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    def mesh(oid, triangles):
        verts = [v for tri in triangles for v in tri]
        vx = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
        tx = "".join(f'<triangle v1="{3*i}" v2="{3*i+1}" v3="{3*i+2}"/>' for i in range(len(triangles)))
        return f'<object id="{oid}" name="geometry_{oid - 1}" type="model"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>'
    inlay = [[(x + 10, y + 10, z) for x, y, z in tri] for tri in box_triangles(10, 10, 0.6)]
    root = (f'<model unit="millimeter" xmlns="{core}"><resources>' + mesh(1, box_triangles(30, 40, 4)) + mesh(2, inlay)
            + '</resources><build><item objectid="1" partnumber="Body_White_Extruder1"/>'
            '<item objectid="2" partnumber="Logo_Black_Extruder2"/></build></model>')
    path = tmp_path / "KeyTag_TwoColour.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", root)
    return path


@pytest.fixture
def mushroom_3mf(tmp_path: Path) -> Path:
    stem = [[(x + 20, y + 20, z) for x, y, z in tri] for tri in box_triangles(5, 5, 40)]
    return make_3mf(tmp_path / "mushroom.3mf", stem + box_triangles(45, 45, 5, z0=40))


@pytest.fixture
def mushroom_stl(tmp_path: Path) -> Path:
    """A thin stem with a wide cap: tall for its base, with a large overhang."""
    stem = [[(x + 20, y + 20, z) for x, y, z in tri] for tri in box_triangles(5, 5, 40)]
    return _stl(tmp_path / "mushroom.stl", stem + box_triangles(45, 45, 5, z0=40))


FAKE_SLICER = r'''#!/usr/bin/env python3
import json, shutil, sys, zipfile
from pathlib import Path
args = sys.argv[1:]
if "--help" in args:
    print("FakeSlicer 9.9.9"); sys.exit(0)
out = Path(args[args.index("--outputdir") + 1])
out.mkdir(parents=True, exist_ok=True)
(out / "argv.json").write_text(json.dumps(args))
if "--export-stl" in args:                     # orient: hand the model back as an STL
    shutil.copyfile(args[-1], out / "obj_1.stl"); sys.exit(0)
with zipfile.ZipFile(args[-1]) as z:
    process = json.loads(z.read("Metadata/project_settings.config"))
if process.get("wall_loops") == "99":
    sys.stderr.write("simulated failure\n"); sys.exit(3)
if process.get("wall_loops") == "95":          # settings file rejected: the real slicer carries on with defaults
    print("[error]   load_from_json: parse _temp_3.config error, invalid json array for x")
if process.get("wall_loops") == "96":          # a setting the slicer did not honour
    process["wall_loops"] = "2"
block = "".join(f"; {k} = {','.join(v) if isinstance(v, list) else v}\n" for k, v in process.items())
pause = ""
with zipfile.ZipFile(args[-1]) as z:
    if "Metadata/custom_gcode_per_layer.xml" in z.namelist():
        import re
        top = re.search(r'top_z="([0-9.]+)"', z.read("Metadata/custom_gcode_per_layer.xml").decode()).group(1)
        pause = f";LAYER_CHANGE\n;Z:{top}\n;PAUSE_PRINT\nM25\n"
body = ("; HEADER_BLOCK_START\n; total estimated time: 1h 23m 45s\n; total layer number: 180\n; HEADER_BLOCK_END\n"
        "; CONFIG_BLOCK_START\n" + block + "; CONFIG_BLOCK_END\nG1 X0\n" + pause +
        "; filament used [mm] = 5000.5\n; filament used [g] = 15.25\n")
if process.get("wall_loops") == "98":          # killed part-way through writing
    (out / "plate_1.gcode").write_text(body[:60]); sys.exit(-11)
(out / "plate_1.gcode").write_text(body + ";end_gcode\n; EXECUTABLE_BLOCK_END\n")
if process.get("wall_loops") == "97":          # finishes the file, then crashes on the way out
    import os, signal
    os.kill(os.getpid(), signal.SIGKILL)   # not SIGSEGV: that pops a macOS crash report each run
'''


@pytest.fixture
def fake_env(tmp_path: Path, profiles_dir: Path, monkeypatch) -> Path:
    slicer = tmp_path / "fake-slicer"
    slicer.write_text(FAKE_SLICER.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    slicer.chmod(slicer.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ORCA_SLICER_PATH", str(slicer))
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(profiles_dir))
    monkeypatch.setenv("C5_OUTPUT_DIR", str(tmp_path / "out"))
    # The print check is an outside tool: off here, tested with a recorded result and in test_real_slicer.py.
    monkeypatch.setattr("creator5_mcp.printcheck.binary", lambda: None)
    return tmp_path
