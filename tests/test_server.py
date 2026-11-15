import asyncio
import json
import os
import sys
from pathlib import Path

from creator5_mcp import server


def test_slice_model_end_to_end_with_fake_slicer(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "black PLA", strength="functional", copies=2, job_name="My Part!")
    assert result["ok"], result
    assert result["summary"] == {"print_time_seconds": 5025, "print_time": "1h 23m", "filament_used_g": 15.25,
                                 "filament_used_mm": 5000.5, "layers": 180}
    job = Path(result["job_dir"])
    assert job.name.endswith("_My_Part") and Path(result["gcode"]).is_file()
    assert result["gcode_complete"] and result["exit_code"] == 0 and result["notes"] == []
    # A project file carries the model, both copies and the changed settings.
    import zipfile
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        model_xml = archive.read("3D/3dmodel.model").decode()
    assert settings["print_settings_id"] == "0.20mm Standard @FF C5" and settings["wall_loops"] == "4"
    assert settings["filament_settings_id"] == ["Flashforge PLA Basic @FF C5"] and settings["filament_colour"] == ["#161616"]
    assert "wall_loops" in settings["different_settings_to_system"][0].split(";")
    assert settings["inherits_group"] == ["", "", ""] and "inherits" not in settings
    assert model_xml.count("<item objectid") == 2 and "BambuStudio-01.10.01.50" in model_xml
    assert settings["sparse_infill_density"] == "25%" and settings["filament_density"] == ["1.24"]
    assert settings["filament_diameter"] == ["1.75"]
    # The slicer sliced that same project, taking its settings from it, and every chosen setting reached the G-code.
    argv = json.loads((job / "argv.json").read_text())
    assert argv[-1] == result["project_3mf"] and "--load-settings" not in argv and "--export-3mf" not in argv
    assert argv[argv.index("--orient") + 1] == "0" and argv[argv.index("--arrange") + 1] == "0"
    assert result["settings_mismatched"] == [] and result["settings_checked_in_gcode"] >= 8


def test_overrides_apply_last_and_unknown_keys_are_flagged(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PETG", process_overrides={"wall_loops": 7, "wal_loops": 1},
                                filament_overrides={"nozzle_temperature": 250})
    assert result["ok"]
    settings = _project_settings(result)
    assert settings["wall_loops"] == "7" and settings["nozzle_temperature"] == ["250"]
    assert "wall_loops" in settings["different_settings_to_system"][0].split(";")
    assert any("wal_loops" in w for w in result["plan"]["warnings"])


def test_label_figures_reach_the_slicer(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "Creality Ender PLA", bed_temp=50)
    settings = _project_settings(result)
    assert settings["nozzle_temperature"] == ["215"] and settings["hot_plate_temp"] == ["50"]
    assert settings["filament_max_volumetric_speed"] == ["6.7"]


def test_setting_missing_from_gcode_is_reported(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", process_overrides={"wall_loops": 96})
    assert result["ok"] and result["settings_mismatched"] == ["wall_loops: asked for 96, G-code has 2"]
    assert any("did not reach the G-code" in w for w in result["plan"]["warnings"])


def test_designer_3mf_becomes_a_project_with_designer_supports(fake_env, tmp_path):
    from conftest import make_3mf, box_triangles
    model_3mf = make_3mf(tmp_path / "holder.3mf", box_triangles(30, 20, 10), {"enable_support": "1", "support_threshold_angle": "25"})
    result = server.slice_model(str(model_3mf), "black PLA")
    assert result["ok"] and result["project_3mf"], result
    settings = _project_settings(result)
    assert settings["enable_support"] == "1" and settings["support_threshold_angle"] == "25"
    assert result["model"]["size_mm"] == [30.0, 20.0, 10.0] and result["settings_mismatched"] == []


def test_auto_orient_builds_project_and_gcode_from_one_oriented_file(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", auto_orient=True)
    assert result["ok"] and any("orientation" in n for n in result["notes"])
    job = Path(result["job_dir"])
    orient_argv = json.loads((job / "oriented" / "argv.json").read_text())
    assert orient_argv[orient_argv.index("--orient") + 1] == "1" and "--export-stl" in orient_argv
    assert result["model"]["file"] == str(job / "oriented" / "cube.stl")


def test_step_file_is_refused_with_what_to_do(fake_env, tmp_path):
    step = tmp_path / "bracket.step"
    step.write_text("ISO-10303-21;")
    error = server.slice_model(str(step), "PLA")["error"]
    assert "STL, OBJ and 3MF only" in error and "export it as STL" in error


def test_spool_register_fills_in_the_label_and_colour(fake_env, cube_stl, capsys):
    from creator5_mcp import cli
    assert not server.save_spool("mystery", "unobtainium")["ok"]
    assert cli.main(["--save-spool", "black-pla", "--filament", "ender-pla", "--nozzle-temp", "210",
                     "--bed-temp", "55", "--max-speed", "60", "--colour", "black"]) == 0
    assert server.list_filaments()["spools"]["black pla"]["filament"] == "ender pla"
    result = server.slice_model(str(cube_stl), "the black PLA")
    settings = _project_settings(result)
    assert settings["nozzle_temperature"] == ["210"] and settings["hot_plate_temp"] == ["55"]
    assert settings["filament_max_volumetric_speed"] == ["5"] and settings["filament_colour"] == ["#161616"]
    assert settings["filament_settings_id"] == ["Generic PLA @FF C5"]
    assert any("register" in r for r in result["plan"]["rationale"])
    # A figure given with the request beats the register; other PLA is untouched.
    assert _project_settings(server.slice_model(str(cube_stl), "black pla", nozzle_temp=200))["nozzle_temperature"] == ["200"]
    assert _project_settings(server.slice_model(str(cube_stl), "white PLA"))["nozzle_temperature"] == ["210"]


def test_multi_colour_3mf_keeps_objects_parts_and_toolheads(fake_env, colour_3mf):
    import zipfile
    result = server.slice_model(str(colour_3mf), "black PLA", other_filaments=["white PETG"], copies=2)
    assert result["ok"], result
    assert result["objects"] == ["Figure", "Plinth"] and result["model"]["filaments_needed"] == 3
    settings = _project_settings(result)
    # Three toolheads: two named, the third filled from toolhead 1 in the file's colour.
    assert settings["filament_settings_id"] == ["Flashforge PLA Basic @FF C5", "Flashforge PETG Pro @FF C5", "Flashforge PLA Basic @FF C5"]
    assert settings["filament_colour"] == ["#161616", "#F2F2F2", "#C89B3C"]
    assert settings["nozzle_temperature"] == ["210", "255", "210"] and settings["filament_diameter"] == ["1.75"] * 3
    assert settings["enable_prime_tower"] == "1" and len(settings["different_settings_to_system"]) == 5
    assert settings["inherits_group"] == [""] * 5
    assert any("toolhead 3 (#C89B3C)" in w for w in result["plan"]["warnings"])
    assert any("Dry the spool" in w for w in result["plan"]["warnings"])          # PETG's warning carried over
    assert settings["support_top_z_distance"] == "0.25"                           # and PETG's wider gap above supports
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        config = archive.read("Metadata/model_settings.config").decode()
        model_xml = archive.read("3D/3dmodel.model").decode()
    assert config.count("<object ") == 2 and config.count("<part ") == 3
    assert '<metadata key="name" value="Figure"/>' in config and '<metadata key="name" value="T2 White"/>' in config
    assert [int(v) for v in __import__("re").findall(r'key="extruder" value="(\d)"', config)] == [1, 1, 2, 3, 3]
    assert model_xml.count("<item objectid") == 4 and model_xml.count("<component ") == 3
    assert 'paint_color="8"' in model_xml
    assert result["settings_mismatched"] == []


def test_too_many_filaments_and_orienting_several_parts(fake_env, colour_3mf, cube_stl):
    assert "4 toolheads" in server.slice_model(str(cube_stl), "PLA", other_filaments=["PLA"] * 4)["error"]
    result = server.slice_model(str(colour_3mf), "PLA", auto_orient=True)
    assert result["ok"] and any("left as designed" in n for n in result["notes"])
    assert not (Path(result["job_dir"]) / "oriented").exists()


def test_job_and_object_take_the_designers_name_not_the_file_hash(fake_env, tmp_path):
    from conftest import make_3mf, box_triangles
    result = server.slice_model(str(make_3mf(tmp_path / "93b857fe1d3a.3mf", box_triangles(30, 20, 10))), "PLA")
    assert result["objects"] == ["holder v3"] and Path(result["job_dir"]).name.endswith("_holder_v3")


def _project_settings(result: dict) -> dict:
    import zipfile
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        return json.loads(archive.read("Metadata/project_settings.config"))


def test_slicer_failure_is_reported_not_raised(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", process_overrides={"wall_loops": 99})
    assert not result["ok"] and "simulated failure" in result["error"] and "code 3" in result["error"]


def test_crash_after_complete_gcode_is_usable_but_noted(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", process_overrides={"wall_loops": 97})
    assert result["ok"] and result["gcode_complete"] and result["exit_code"] != 0
    assert "complete" in result["notes"][0]


def test_unfinished_gcode_is_rejected(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", process_overrides={"wall_loops": 98})
    assert not result["ok"] and "unfinished" in result["error"] and "Do not print" in result["error"]


def test_wrong_filament_diameter_is_refused(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", filament_overrides={"filament_diameter": 2.85})
    assert not result["ok"] and "1.75" in result["error"]


def test_missing_model_and_unknown_filament(fake_env, cube_stl):
    assert "not found" in server.slice_model("/nope/x.stl", "PLA")["error"]
    assert "not recognised" in server.slice_model(str(cube_stl), "unobtainium")["error"]


def test_check_setup_and_missing_slicer(fake_env, monkeypatch):
    report = server.check_setup()
    assert report["ok"] and report["slicer_version"] == "FakeSlicer 9.9.9" and "printer_configured" not in report
    assert report["nozzles"]["0.4"] == ["0.12mm Standard @FF C5", "0.20mm Standard @FF C5", "0.24mm Standard @FF C5"]
    monkeypatch.setenv("ORCA_SLICER_PATH", "/nope/slicer")
    monkeypatch.setattr("creator5_mcp.config._candidates", lambda: [])
    assert not server.check_setup()["ok"]


def test_slicer_with_readable_presets_beats_one_with_packed_presets(tmp_path, profiles_dir, monkeypatch):
    """OrcaSlicer 2.5 packs presets into .opc files; a slicer with plain JSON presets must win."""
    from creator5_mcp import config
    orca, studio = tmp_path / "orca-bin", tmp_path / "studio-bin"
    orca.write_text(""); studio.write_text("")
    packed = tmp_path / "packed"
    (packed / "Flashforge").mkdir(parents=True)          # cover images only, no machine/ folder
    (packed / "Flashforge.opc").write_bytes(b"ZCRO")
    for key in ("ORCA_SLICER_PATH", "ORCA_PROFILES_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_candidates", lambda: [(orca, [packed]), (studio, [profiles_dir])])
    settings = config.load_settings()
    assert (settings.slicer_path, settings.profiles_dir) == (studio, profiles_dir)
    # Only the packed slicer installed: slicer is found, presets are not, and the fix is explained.
    monkeypatch.setattr(config, "_candidates", lambda: [(orca, [packed])])
    settings = config.load_settings()
    assert settings.slicer_path == orca and settings.profiles_dir is None
    report = server.check_setup()
    assert not report["ok"] and ".opc" in report["fix"]
    # An explicit presets folder is honoured with whichever slicer is found.
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(profiles_dir))
    settings = config.load_settings()
    assert (settings.slicer_path, settings.profiles_dir) == (orca, profiles_dir)


def test_tools_over_real_stdio_transport(fake_env, cube_stl):
    """Start the server as a subprocess and call it as an MCP client would."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        env = {**os.environ, "PYTHONPATH": str(Path(server.__file__).parents[1])}
        params = StdioServerParameters(command=sys.executable, args=["-m", "creator5_mcp.server"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                reply = await session.call_tool("slice_model", {"model_path": str(cube_stl), "filament": "PLA"})
                return names, json.loads(reply.content[0].text)

    names, payload = asyncio.run(run())
    assert names == {"check_setup", "list_filaments", "analyse_model", "recommend_settings", "slice_model",
                     "open_in_slicer", "save_spool", "record_print_result", "compare_with_saved_project"}
    assert payload["ok"] and payload["summary"]["print_time"] == "1h 23m"


def test_claude_desktop_installer_merges_and_backs_up(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "installer", Path(__file__).parents[1] / "scripts" / "install_claude_desktop.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    config = tmp_path / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"theme": "dark", "mcpServers": {
        "other": {"command": "x"}, "creator5-slicer": {"command": "/old", "env": {"C5_PRINTER_IP": "1.2.3.4"}}}}))
    monkeypatch.setenv("CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setattr(installer, "server_command", lambda: Path("/venv/bin/creator5-slicer-mcp"))
    assert installer.main() == 0 and installer.main() == 0      # safe to run twice
    data = json.loads(config.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["creator5-slicer"] == {"command": "/venv/bin/creator5-slicer-mcp",
                                                     "env": {"C5_PRINTER_IP": "1.2.3.4"}}
    assert list(config.parent.glob("*.backup-*"))
    # A missing config file is created; a broken one is left untouched.
    config.unlink()
    assert installer.main() == 0 and "creator5-slicer" in json.loads(config.read_text())["mcpServers"]
    config.write_text("{not json")
    import pytest
    with pytest.raises(SystemExit, match="Nothing was changed"):
        installer.main()
    assert config.read_text() == "{not json"


def test_cli_slices_without_needing_quotes(fake_env, cube_stl, capsys):
    from creator5_mcp import cli
    assert cli.main([str(cube_stl), "--filament", "ender-pla", "--strength", "functional", "--copies", "2"]) == 0
    out = capsys.readouterr().out
    assert "Creality Ender PLA" in out and "wall_loops             4" in out and "nozzle_temperature     215" in out
    assert "1h 23m" in out and "15.25 g" in out and "Sliced 2 x cube" in out
    assert cli.main(["/nope.stl", "--filament", "PLA"]) == 1 and "FAILED" in capsys.readouterr().out
    assert cli.main([str(cube_stl), "--smooth-top"]) == 0 and "ironing_type           top" in capsys.readouterr().out




def test_supports_are_checked_in_the_gcode_not_only_the_setting(fake_env, cube_stl, mushroom_stl):
    # The fake slicer prints no support, as the real one does when nothing needs it.
    on = server.slice_model(str(cube_stl), "PLA", supports="on")
    assert on["supports_on"] and on["support_sections_in_gcode"] == 0
    assert any("none will print" in n for n in on["notes"])
    off = server.slice_model(str(mushroom_stl), "PLA", supports="off")
    assert not off["supports_on"] and any("over thin air" in w for w in off["plan"]["warnings"])
    assert not any("over thin air" in w for w in server.slice_model(str(cube_stl), "PLA")["plan"]["warnings"])


def test_print_result_is_recorded_with_what_was_changed_by_hand(fake_env, cube_stl, capsys):
    import zipfile
    from creator5_mcp import cli
    job = server.slice_model(str(cube_stl), "black PLA", strength="functional")
    # The owner turned supports on, cloned the part and saved the project from the slicer.
    saved = Path(job["job_dir"]) / "saved.3mf"
    with zipfile.ZipFile(job["project_3mf"]) as src, zipfile.ZipFile(saved, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "Metadata/project_settings.config":
                edited = json.loads(data); edited["enable_support"] = "1"; edited["an_extra_default"] = "x"
                # Flash Studio pads lists to four toolheads and rewrites numbers; neither is a change.
                edited["nozzle_temperature"] = edited["nozzle_temperature"] * 4
                edited["line_width"] = "0.420"
                data = json.dumps(edited).encode()
            elif name == "3D/3dmodel.model":
                item = next(line for line in data.decode().splitlines() if "<item " in line)
                data = data.decode().replace(item, item + "\n" + item).encode()
            dst.writestr(name, data)
    assert server.compare_with_saved_project(job["project_3mf"], str(saved))["changed_by_hand"] == {
        "settings": {"enable_support": {"written": "0", "printed": "1"}}, "parts_on_plate": {"written": 1, "printed": 2}}
    done = server.record_print_result(job["project_3mf"], "good", "clean walls", str(saved))
    assert done["ok"] and done["results_recorded"] == 1
    entry = done["result"]
    assert entry["process_preset"] == "0.20mm Standard @FF C5" and entry["changed"]["wall_loops"] == "4"
    assert entry["changed_by_hand"]["settings"]["enable_support"]["printed"] == "1"
    assert cli.main(["--record-result", job["project_3mf"], "--outcome", "acceptable", "--notes", "stringy", "supports"]) == 0
    assert "2 results so far" in capsys.readouterr().out
    assert "outcome must be" in server.record_print_result(job["project_3mf"], "perfect")["error"]
    assert "not a slicer project" in server.record_print_result(str(cube_stl), "good")["error"]
