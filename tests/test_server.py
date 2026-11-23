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


def test_process_changes_are_also_written_on_the_object(fake_env, cube_stl):
    # Flash Studio's sign-in can select the process preset again and drop its changes; an object's own settings stay.
    import zipfile
    result = server.slice_model(str(cube_stl), "PETG", strength="functional", supports="on",
                                process_overrides={"initial_layer_speed": 20})
    assert result["ok"], result
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        config = archive.read("Metadata/model_settings.config").decode()
    assert '<metadata key="wall_loops" value="4"/>' in config and '<metadata key="enable_support" value="1"/>' in config
    # Settings that exist only on the preset stay there, and the user is told to check them.
    assert "initial_layer_speed" not in config and "enable_prime_tower" not in config
    reset = [w for w in result["plan"]["warnings"] if "signs in" in w]
    assert len(reset) == 1 and "initial_layer_speed" in reset[0] and "enable_prime_tower" not in reset[0]


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


def test_step_file_is_converted_once_and_sliced(fake_env, tmp_path):
    import pytest
    pytest.importorskip("cascadio")
    from conftest import write_step_box
    result = server.slice_model(str(write_step_box(tmp_path / "bracket.step", 40, 25, 12)), "PLA")
    assert result["ok"], result
    assert result["model"]["size_mm"] == [40.0, 25.0, 12.0] and result["model"]["volume_cm3"] == 12.0
    assert result["objects"] == ["bracket"] and any("STEP" in n for n in result["notes"])
    job = Path(result["job_dir"])
    assert job.name.endswith("_bracket") and (job / "bracket.obj").is_file()
    assert not list(job.parent.glob("*_converting"))
    assert server.analyse_model(str(tmp_path / "bracket.step"))["size_mm"] == [40.0, 25.0, 12.0]


def test_step_without_the_converter_or_with_a_bad_file_says_what_to_do(fake_env, tmp_path, monkeypatch):
    import importlib.util
    bad = tmp_path / "broken.step"
    bad.write_text("not a step file")
    if importlib.util.find_spec("cascadio") is not None:
        assert "could not be read as a STEP file" in server.slice_model(str(bad), "PLA")["error"]
        assert not list((fake_env / "out").glob("*_converting"))
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "cascadio" else real(name, *a))
    error = server.slice_model(str(bad), "PLA")["error"]
    assert "pip install cascadio" in error and "export it as STL" in error


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


def test_a_proven_flow_limit_holds_at_every_layer_height(fake_env, cube_stl):
    server.save_spool("black pla", "ender pla", 215, 60, 80, "black", max_flow=6.7)
    for quality in ("standard", "fine"):       # 0.2 mm and 0.12 mm layers
        settings = _project_settings(server.slice_model(str(cube_stl), "black pla", quality=quality))
        assert settings["filament_max_volumetric_speed"] == ["6.7"], quality
    # Without the proven limit the label's speed gives a cap that shrinks with the layer.
    server.save_spool("black pla", "ender pla", 215, 60, 80, "black")
    assert _project_settings(server.slice_model(str(cube_stl), "black pla", quality="fine"))["filament_max_volumetric_speed"] == ["4"]
    # A speed given with the request beats the saved flow limit; a silly limit is refused.
    server.save_spool("black pla", "ender pla", max_flow=6.7)
    assert _project_settings(server.slice_model(str(cube_stl), "black pla", max_speed=40))["filament_max_volumetric_speed"] == ["3.4"]
    server.save_spool("black pla", "ender pla", max_flow=500)
    assert "max_flow" in server.slice_model(str(cube_stl), "black pla")["error"]


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
    assert settings["filament_self_index"] == ["1", "2", "3"]
    assert settings["inherits_group"] == [""] * 5
    assert any("toolhead 3 (#C89B3C)" in w for w in result["plan"]["warnings"])
    assert any("Dry the spool" in w for w in result["plan"]["warnings"])          # PETG's warning carried over
    assert settings["support_top_z_distance"] == "0.25"                           # and PETG's wider gap above supports
    assert not any("bed will run" in w for w in result["plan"]["warnings"])       # this fixture's presets share one bed temperature
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        config = archive.read("Metadata/model_settings.config").decode()
        model_xml = archive.read("3D/3dmodel.model").decode()
    assert config.count("<object ") == 2 and config.count("<part ") == 3
    assert '<metadata key="name" value="Figure"/>' in config and '<metadata key="name" value="T2 White"/>' in config
    assert [int(v) for v in __import__("re").findall(r'key="extruder" value="(\d)"', config)] == [1, 1, 2, 3, 3]
    assert model_xml.count("<item objectid") == 4 and model_xml.count("<component ") == 3
    assert 'paint_color="8"' in model_xml
    assert result["settings_mismatched"] == []


def test_only_toolhead_1_carries_a_spools_own_figures(fake_env, colour_3mf):
    """Flash Studio swaps any changed filament on toolheads 2 to 4 for a generic preset, so those get the
    nearest stock preset: nearest bed first, since the bed is shared, then nearest nozzle."""
    server.save_spool("my pla", "PLA", nozzle_temp=205, bed_temp=55)
    server.save_spool("warm pla", "PLA", nozzle_temp=222, bed_temp=65)
    result = server.slice_model(str(colour_3mf), "my pla", other_filaments=["warm pla", "PLA"])
    assert result["ok"] and result["settings_mismatched"] == [], result
    settings = _project_settings(result)
    # Toolhead 1 keeps 205 and 55; toolhead 2 becomes the stock preset nearest 65 and 222; toolhead 3 is untouched.
    assert settings["filament_settings_id"] == ["Flashforge PLA Basic @FF C5", "Generic PLA @FF C5", "Flashforge PLA Basic @FF C5"]
    assert settings["nozzle_temperature"] == ["205", "220", "210"] and settings["textured_plate_temp"] == ["55", "65", "65"]
    changed = settings["different_settings_to_system"]
    assert "nozzle_temperature" in changed[1] and changed[2:] == ["", "", ""]
    assert any("Toolhead 2" in w and "Generic PLA @FF C5" in w and "222 C and 65 C were asked for" in w for w in result["plan"]["warnings"])
    # The bed is shared and the hottest wins, which the plan says when toolheads are 10 C or more apart.
    assert any("bed will run at 65 C" in w and "toolhead 1" in w for w in result["plan"]["warnings"])


def test_check_setup_says_which_code_is_running(fake_env, monkeypatch):
    report = server.check_setup()
    assert report["server_version"] == "0.2.0" and report["restart_needed"] is False
    assert report["code_revision"] == server._STARTED_AT_REVISION
    # The code on disk moved on after the server started.
    monkeypatch.setattr(server, "_code_revision", lambda: "abc1234")
    assert server.check_setup()["restart_needed"] is True


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


def test_rejected_settings_file_is_an_error_not_a_default_print(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", process_overrides={"wall_loops": 95})
    assert not result["ok"] and "could not read the project's settings" in result["error"]


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
                     "open_in_slicer", "save_spool", "record_print_result", "compare_with_saved_project",
                     "check_flash_studio_slice"}
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


def test_part_number_labels_give_toolheads_and_touching_parts_become_one_object(fake_env, labelled_3mf, colour_3mf):
    import zipfile
    from creator5_mcp import model
    loaded = model.load_objects(labelled_3mf)
    assert [(o["name"], [(p["name"], p["extruder"]) for p in o["parts"]]) for o in loaded["objects"]] == [
        ("KeyTag_TwoColour", [("Body White Extruder1", 1), ("Logo Black Extruder2", 2)])]
    # Parts that sit apart stay separate objects.
    assert [o["name"] for o in model.load_objects(colour_3mf)["objects"]] == ["Figure", "Plinth"]
    result = server.slice_model(str(labelled_3mf), "white PETG", other_filaments=["black PETG"])
    assert result["ok"] and result["objects"] == ["KeyTag_TwoColour"] and len(result["plan"]["filaments"]) == 2
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        config = archive.read("Metadata/model_settings.config").decode()
    assert config.count("<object ") == 1 and config.count("<part ") == 2
    assert [int(v) for v in __import__("re").findall(r'key="extruder" value="(\d)"', config)] == [1, 1, 2]


def test_pause_before_a_height_is_written_and_found_in_the_gcode(fake_env, cube_stl):
    import zipfile
    result = server.slice_model(str(cube_stl), "PLA", pause_before_z=3.0)
    assert result["ok"] and result["pauses_at_z"] == [3.0] and any("pauses before the layer at 3 mm" in n for n in result["notes"])
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        xml = archive.read("Metadata/custom_gcode_per_layer.xml").decode()
    assert 'top_z="3"' in xml and 'type="1"' in xml and 'gcode="M25"' in xml and '<mode value="SingleExtruder"/>' in xml
    plain = server.slice_model(str(cube_stl), "PLA")
    assert plain["pauses_at_z"] == [] and "Metadata/custom_gcode_per_layer.xml" not in zipfile.ZipFile(plain["project_3mf"]).namelist()


def test_the_print_log_is_read_back_when_recommending(fake_env, cube_stl):
    first = server.slice_model(str(cube_stl), "PETG")
    assert first["ok"] and first["plan"]["earlier_prints"] == []
    server.record_print_result(first["project_3mf"], "acceptable", "Lettering smudged and the surfaces feel rough.")
    again = server.recommend_settings("PETG", str(cube_stl))["plan"]
    assert [p["outcome"] for p in again["earlier_prints"]] == ["acceptable"] and again["earlier_prints"][0]["same_model"]
    assert any("this model" in w and "rated acceptable: Lettering smudged" in w for w in again["warnings"])
    # Another model in the same filament still hears about it; another filament does not.
    assert any("this filament preset" in w for w in server.recommend_settings("PETG")["plan"]["warnings"])
    assert server.recommend_settings("PLA")["plan"]["earlier_prints"] == []
    server.record_print_result(first["project_3mf"], "good", "Clean.")
    latest = server.recommend_settings("PETG", str(cube_stl))["plan"]
    assert not any("rated" in w for w in latest["warnings"]) and any("rated good" in r for r in latest["rationale"])


def test_flash_studios_own_slice_is_found_by_its_origin_and_compared_with_the_project(fake_env, cube_stl, tmp_path):
    import shutil
    import zipfile
    from creator5_mcp import slicer
    result = server.slice_model(str(cube_stl), "PETG", nozzle_temp=250)
    project = Path(result["project_3mf"])
    root = tmp_path / "recovery"
    assert slicer.studio_gcode(project, [root]) is None
    for name, origin in (("other", tmp_path / "another.3mf"), ("mine", project)):
        session = root / f"20261117T1#{name}"
        (session / "Metadata").mkdir(parents=True)
        (session / "origin.txt").write_text(str(origin))
        shutil.copy(result["gcode"], session / "Metadata" / ".1.0.gcode")
    found = slicer.studio_gcode(project, [root])
    assert found == root / "20261117T1#mine" / "Metadata" / ".1.0.gcode"
    with zipfile.ZipFile(project) as archive:
        asked = json.loads(archive.read("Metadata/project_settings.config"))
    report = slicer.studio_report(found, asked)
    assert report["differs_from_project"] == [] and report["toolheads"][0]["nozzle_c"] == 250
    # Flash Studio swapping the filament shows up as a difference.
    found.write_text(found.read_text().replace("; nozzle_temperature = 250", "; nozzle_temperature = 235"))
    swapped = slicer.studio_report(found, asked)
    assert swapped["toolheads"][0]["nozzle_c"] == 235
    assert swapped["differs_from_project"] == ["nozzle_temperature: project has 250, Flash Studio sliced 235"]


def test_setup_reports_a_flash_studio_release_other_than_the_one_tested(fake_env, tmp_path, monkeypatch):
    import plistlib
    from creator5_mcp import slicer
    assert "warning" not in server.check_setup()          # not an app bundle: nothing to compare
    app = tmp_path / "Flash Studio.app" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "9.9"}))
    binary = app / "MacOS" / "Flash Studio"
    binary.write_bytes(Path(os.environ["ORCA_SLICER_PATH"]).read_bytes())
    binary.chmod(0o755)
    monkeypatch.setenv("ORCA_SLICER_PATH", str(binary))
    report = server.check_setup()
    assert report["flash_studio_version"] == "9.9" and report["tested_with"] == slicer.TESTED_FLASH_STUDIO
    assert "Flash Studio is now 9.9" in report["warning"]


FAKE_CAD = '''#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
with open(here / "cad_calls.txt", "a") as log:
    log.write(" ".join(args) + "\\n")
if args[:2] == ["server", "start"]:
    print("started the cad server (cad 0.11.17)"); sys.exit(0)
if args[:2] == ["server", "stop"]:
    sys.exit(0)
if args[:2] == ["step", "export"]:
    Path(args[3]).write_text("solid"); sys.exit(0)
mode = (here / "cad_mode.txt").read_text()
if mode == "not_running" or (mode == "slow_step" and args[0] == "step"):
    print(json.dumps({{"ok": False, "error": "the cad server stopped", "reason": "not_running", "status": "not_checked"}})); sys.exit(3)
way = lambda name, rank, support: {{"orientation": name, "rank": rank, "rotate": "bd.Rot(90, 0, 0)" if rank == 1 else "bd.Rot(0, 0, 0)",
                                   "support_volume_mm3": support, "overhang_area_mm2": 1.0, "build_height_mm": 10.0, "bed_contact_mm2": 5.0}}
if "--candidates" in args:
    print(json.dumps({{"status": "ok", "findings": [], "measurements": {{"orientations": {{"candidates": [
        way("rot_plus_90_x", 1, 200.0), way("current_plus_z", 2, 4200.0)]}}}}}}))
    sys.exit(0)
wall = {{"severity": "error", "code": "printing.wall_supported", "message": "supported wall thickness 0.5 mm is under the 0.8 mm minimum",
        "location": "1, 2, 3", "hint": "thicken it to at least 0.8 mm", "source": "cad, its own figure"}}
print(json.dumps({{"status": "failed" if mode == "failed" else "ok", "reason": None, "summary": "summary line",
    "findings": ([wall, wall] if mode == "failed" else []) + [
        {{"severity": "info", "code": "families_not_measured", "message": "pins, details are not measured on this source",
          "location": "rules", "hint": "check the STEP", "source": None}},
        {{"severity": "info", "code": "rules_general", "message": "general rules", "location": "rules", "hint": None, "source": None}}],
    "measurements": {{}}}}))
sys.exit(1 if mode == "failed" else 0)
'''


def test_print_check_reads_cads_result_and_never_calls_an_unchecked_part_fine(fake_env, cube_stl, monkeypatch):
    from creator5_mcp import printcheck
    # No cad command: the slice still works, and says the shape was not checked.
    missing = server.slice_model(str(cube_stl), "PLA")
    assert missing["ok"] and missing["print_check"]["checked"] is False and "uv tool install cad" in missing["print_check"]["reason"]
    assert "not installed" in server.check_setup()["print_check_tool"]
    cad = fake_env / "cad"
    cad.write_text(FAKE_CAD.format(python=sys.executable))
    cad.chmod(0o755)
    monkeypatch.setattr(printcheck, "binary", lambda: str(cad))
    (fake_env / "cad_mode.txt").write_text("failed")
    result = server.slice_model(str(cube_stl), "silk PLA")
    check = result["print_check"]
    assert check["checked"] and check["status"] == "failed" and check["exact"] is False
    assert check["errors"] == ["Supported wall thickness 0.5 mm is under the 0.8 mm minimum (2 places). Fix: thicken it to at least 0.8 mm. "
                               "At x, y, z = 1, 2, 3 mm. Limit from: cad, its own figure."]
    # What a mesh cannot be judged for is said, and the general-rules boilerplate is not.
    assert len(check["not_judged"]) == 2 and "pins, details" in check["not_judged"][0].lower() and "sampling" in check["not_judged"][1]
    # Another way up that saves 4 cm³ of support is worth a warning; both reach the plan the user is read.
    assert check["orientation"]["best_is_as_it_stands"] is False
    warnings = [w for w in result["plan"]["warnings"] if w.startswith("Print check: ")]
    assert len(warnings) == 2 and "4.0 cm³ less support" in warnings[1] and "auto_orient" in warnings[1]
    # The plan's layer height, the nozzle, the bed and the material go to cad, and the server it started is stopped.
    calls = (fake_env / "cad_calls.txt").read_text().splitlines()
    assert calls[0] == "server start" and calls[-1] == "server stop" and len(calls) == 4
    assert f"--build-volume 256x256x256 --nozzle 0.4 --layer-height {result['plan']['layer_height_mm']:g} --json --material pla" in calls[1]
    assert calls[1].startswith("stl dfm ") and "--material" not in calls[2]
    # A check that did not run is not a pass, and adds no warning of its own.
    (fake_env / "cad_mode.txt").write_text("not_running")
    stopped = server.recommend_settings("PLA", str(cube_stl))
    assert stopped["print_check"] == {"checked": False, "reason": "the cad server stopped"}
    assert not any(w.startswith("Print check") for w in stopped["plan"]["warnings"])
    assert server.analyse_model(str(fake_env / "nothing.obj"))["ok"] is False
    # A STEP whose exact check does not finish is checked as a mesh of itself, and the result says which it was.
    (fake_env / "cad_mode.txt").write_text("slow_step")
    part = fake_env / "part.step"
    part.write_text("ISO-10303-21;")
    meshed = printcheck.check(part)
    assert meshed["checked"] and meshed["exact"] is False and meshed["status"] == "ok"
    assert any("exact check of the STEP did not finish (the cad server stopped)" in line for line in meshed["not_judged"])
    calls = (fake_env / "cad_calls.txt").read_text().splitlines()
    assert [c.split()[:2] for c in calls[-7:]] == [["server", "start"], ["step", "dfm"], ["server", "stop"], ["server", "start"],
                                                   ["step", "export"], ["stl", "dfm"], ["stl", "dfm"]] or \
           [c.split()[:2] for c in calls[-8:-1]] == [["server", "start"], ["step", "dfm"], ["server", "stop"], ["server", "start"],
                                                     ["step", "export"], ["stl", "dfm"], ["stl", "dfm"]]
