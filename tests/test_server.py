import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

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
    assert report["server_version"] == "0.3.0" and report["restart_needed"] is False
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
                payload = json.loads(reply.content[0].text)
                picture = await session.call_tool("preview", {"project_3mf": payload["project_3mf"], "view": "top"})
                layer = await session.call_tool("preview", {"project_3mf": payload["project_3mf"], "layer": 1})
                # The pictures cross the protocol as images, not as text about images.
                assert [c.type for c in picture.content] == ["image"] and (getattr(picture.content[0], "mime_type", None) or picture.content[0].mimeType) == "image/png"
                assert [c.type for c in layer.content] == ["image"]
                return names, payload

    names, payload = asyncio.run(run())
    assert names == {"check_setup", "list_filaments", "analyse_model", "recommend_settings", "slice_model",
                     "open_in_slicer", "save_spool", "record_print_result", "compare_with_saved_project",
                     "check_flash_studio_slice", "preview", "printer_status", "reprint_objects", "compare_settings", "flow_test", "record_flow_test", "temperature_test", "record_temperature_test",
                     "pressure_advance_test", "record_pressure_advance_test"}
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


def _pixels(png: bytes):
    import io
    import numpy as np
    from PIL import Image
    return np.array(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)


def test_the_plate_and_the_first_layer_are_drawn_with_every_slice(fake_env, cube_stl, colour_3mf):
    from creator5_mcp import preview, slicer
    result = server.slice_model(str(cube_stl), "red PLA", copies=2)
    assert result["ok"] and result["notes"] == [], result
    assert Path(result["plate_png"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" == Path(result["first_layer_png"]).read_bytes()[:8]
    # From above the bed fills 92% of a 900 px picture, so 1 mm is 900 * 0.92 / 256 px and a 20 mm cube is about 65 px wide.
    top = _pixels(preview.plate_png(Path(result["project_3mf"]), "top")[0])
    red = (top[:, :, 0] > 150) & (top[:, :, 1] < 80) & (top[:, :, 2] < 80)
    columns = np_runs(red.any(axis=0))
    assert len(columns) == 2 and all(63 <= b - a <= 67 for a, b in columns), columns     # two copies, each 20 mm wide, apart
    assert tuple(top[5, 5]) == preview.BACKGROUND and tuple(top[450, 60]) == preview.BED_COLOUR
    # Each toolhead's parts take that toolhead's colour: black body, white cap, gold plinth.
    colours = server.slice_model(str(colour_3mf), "black PLA", other_filaments=["white PLA", "gold PLA"])
    seen = _pixels(preview.plate_png(Path(colours["project_3mf"]), "top")[0])
    plan = [slot["colour"] for slot in colours["plan"]["filaments"]]
    for colour in plan[1:]:
        want = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
        assert (abs(seen - want).sum(axis=2) < 40).sum() > 20, colour
    # A black part is lifted to deep grey, so it shows against the dark bed.
    seen = _pixels(preview.plate_png(Path(colours["project_3mf"]), "iso")[0])       # from above the cap hides the body
    grey = (seen[:, :, 0] == seen[:, :, 1]) & (seen[:, :, 1] == seen[:, :, 2]) & (seen[:, :, 0] >= 20) & (seen[:, :, 0] <= 52)
    assert grey.sum() > 20
    # The first layer comes from the G-code: the fake slicer draws a 20 mm square outline at (100, 100).
    found = slicer.layer_moves(Path(result["gcode"]))
    assert found["layer"] == 1 and found["z"] == 0.2 and len(found["moves"]) == 4
    assert found["moves"][0] == (100.0, 100.0, 120.0, 100.0, 0, "Outer wall")
    assert server.preview(str(fake_env / "missing.3mf"))["ok"] is False
    assert "no layer 9" in server.preview(result["project_3mf"], layer=9)["error"]
    assert "iso or top" in server.preview(result["project_3mf"], view="side")["error"]


def np_runs(flags):
    """(start, end) of each run of True in a row of flags."""
    import numpy as np
    edges = np.flatnonzero(np.diff(np.r_[0, flags.astype(int), 0]))
    return list(zip(edges[::2], edges[1::2]))


def test_one_layer_is_read_from_gcode_with_arcs_toolheads_and_features(tmp_path):
    from creator5_mcp import preview, slicer
    gcode = tmp_path / "plate_1.gcode"
    gcode.write_text(
        "; layer_change_gcode = ;LAYER_CHANGE\n"      # a setting that only mentions the marker is not a layer
        "M83\nT0\nG1 X5 Y5 E9\n"                      # start-up purge, before any layer
        ";LAYER_CHANGE\n;Z:0.2\n;TYPE:Brim\nG1 X10 Y10\nG1 X20 Y10 E.5\nG1 X30 Y10\n"
        ";TYPE:Custom\nG1 X40 Y10 E.5\n"              # the slicer's own section: not part of the print
        ";TYPE:Outer wall\nT1\nG1 X40 Y20\nG3 X20 Y20 I-10 J0 E1\nG1 X20 Y30 E-.8\n"   # a half circle, then a retraction
        ";LAYER_CHANGE\n;Z:0.4\n;TYPE:Inner wall\nM82\nG92 E0\nG1 X0 Y0 E1\nG1 X5 Y0 E1\nG1 X9 Y0 E2\n")
    first = slicer.layer_moves(gcode)
    assert first["z"] == 0.2 and first["moves"][0] == (10.0, 10.0, 20.0, 10.0, 0, "Brim")
    arc = [m for m in first["moves"] if m[5] == "Outer wall"]
    assert len(first["moves"]) == 1 + len(arc) and len(arc) > 20 and {m[4] for m in arc} == {1}
    assert max(m[1] for m in arc) == pytest.approx(30, abs=0.05) and arc[-1][2:4] == (20.0, 20.0)   # anticlockwise, over the top
    # By height, and with absolute extrusion: only a move that pushes more filament out is drawn.
    second = slicer.layer_moves(gcode, z=0.3)
    assert second["layer"] == 2 and [m[:4] for m in second["moves"]] == [(40.0, 20.0, 0.0, 0.0), (5.0, 0.0, 9.0, 0.0)] or \
           [m[:4] for m in second["moves"]] == [(20.0, 30.0, 0.0, 0.0), (5.0, 0.0, 9.0, 0.0)]
    with pytest.raises(slicer.SliceError, match="2 layers"):
        slicer.layer_moves(gcode, layer=3)
    # Brim is drawn paler than the wall of the same toolhead.
    picture = _pixels(preview.layer_png(first["moves"], ["#C8102E", "#1F5AA6"], "layer 1"))
    assert ((picture[:, :, 0] > 225) & (picture[:, :, 1] > 140) & (picture[:, :, 1] < 200)).sum() > 50      # pale red brim
    assert ((picture[:, :, 2] > 150) & (picture[:, :, 0] < 60)).sum() > 200                                   # blue wall


def _ring(x0, y0, x1, y1):
    return f"G1 X{x0} Y{y0}\nG1 X{x1} Y{y0} E1\nG1 X{x1} Y{y1} E1\nG1 X{x0} Y{y1} E1\nG1 X{x0} Y{y0} E1\n"


def _gcode(path, layers):
    """layers: for each layer, a list of (feature, toolhead, text)."""
    text = "M83\n"
    for number, parts in enumerate(layers, 1):
        text += f";LAYER_CHANGE\n;Z:{number * 0.2:.1f}\n"
        for feature, tool, body in parts:
            text += f";TYPE:{feature}\nT{tool}\n{body}"
    path.write_text(text)
    return path


def test_the_sliced_result_is_compared_with_the_project(fake_env, cube_stl, labelled_3mf, tmp_path):
    from creator5_mcp import model, slicecheck
    project = Path(server.slice_model(str(cube_stl), "PLA", copies=2)["project_3mf"])       # two 20 x 20 x 10 mm cubes
    first, second = [(o["parts"][0]["tris"].reshape(-1, 3).min(0), o["parts"][0]["tris"].reshape(-1, 3).max(0))
                     for o in model.load_objects(project)["objects"]]
    wall = lambda box, inset=0.2: _ring(box[0][0] + inset, box[0][1] + inset, box[1][0] - inset, box[1][1] - inset)
    both = [("Outer wall", 0, wall(first) + wall(second))]
    # As modelled: nothing to say.
    clean = slicecheck.check(project, _gcode(tmp_path / "clean.gcode", [both] * 50))
    assert clean["checked"] and clean["layers"] == 50 and clean["warnings"] == [] and clean["stops_short_mm"] is None
    # A 4 mm square that begins at layer 20, 8 mm from anything: it starts in mid-air. A line reaching out from a
    # wall on layer 30 is a bridge, joined to what holds it, and is not reported.
    x, y = first[0][0] + 5, first[1][1] + 8                     # clear of both cubes, which stand side by side
    island = ("Outer wall", 0, _ring(x, y, x + 4, y + 4))
    bridge = ("Bridge", 0, f"G1 X{first[1][0] - 0.2} Y{first[0][1] + 15}\nG1 X{first[1][0] + 6} Y{first[0][1] + 15} E1\n")
    layers = [both] * 19 + [both + [island]] * 10 + [both + [island, bridge]] + [both + [island]] * 20
    found = slicecheck.check(project, _gcode(tmp_path / "island.gcode", layers))
    assert [(f["z"], f["object"]) for f in found["mid_air"]] == [(4.0, "no object")] and 4 < found["mid_air"][0]["area_mm2"] < 12
    assert "starts printing in mid-air at 4 mm" in found["warnings"][0] and len(found["warnings"]) == 1
    # The same square with support under it from the first layer stands on something.
    prop = ("Support", 0, _ring(x, y, x + 4, y + 4))
    held = slicecheck.check(project, _gcode(tmp_path / "held.gcode", [both + [prop]] * 19 + [both + [island]] * 31))
    assert held["mid_air"] == [] and held["warnings"] == []
    # One cube never printed, and the print ending 4 mm early.
    partial = slicecheck.check(project, _gcode(tmp_path / "partial.gcode", [[("Outer wall", 0, wall(first))]] * 30))
    assert partial["left_out"] == ["cube"] and partial["stops_short_mm"] == 4.0
    assert any("prints nothing where it stands" in w for w in partial["warnings"]) and any("4 mm below the model's top" in w for w in partial["warnings"])
    # A cube printed 6 mm narrower than its model: its outline has no line beside it.
    narrow = slicecheck.check(project, _gcode(tmp_path / "narrow.gcode", [[("Outer wall", 0, wall(first, 3.2) + wall(second))]] * 50))
    assert narrow["smaller"] and narrow["smaller"][0]["toolhead"] is None and "prints smaller than its model" in narrow["warnings"][0]
    # A two-colour tag whose inlay is printed by the wrong toolhead: toolhead 2's part has no toolhead 2 lines.
    tag = Path(server.slice_model(str(labelled_3mf), "white PLA", other_filaments=["black PLA"])["project_3mf"])
    parts = {part["extruder"]: (part["tris"].reshape(-1, 3).min(0), part["tris"].reshape(-1, 3).max(0))
             for obj in model.load_objects(tag)["objects"] for part in obj["parts"]}
    body = [("Outer wall", 0, wall(parts[1]))]
    right = [body + [("Outer wall", 1, wall(parts[2]))]] * 3 + [body] * 17
    wrong = [body + [("Outer wall", 0, wall(parts[2]))]] * 3 + [body] * 17
    assert slicecheck.check(tag, _gcode(tmp_path / "right.gcode", right))["warnings"] == []
    swapped = slicecheck.check(tag, _gcode(tmp_path / "wrong.gcode", wrong))
    assert {f["toolhead"] for f in swapped["smaller"]} == {2} and "toolhead 2 prints smaller" in swapped["warnings"][0]


def _fake_printer(reply: dict, mode: int = 0, pid: int = 40):
    """A Creator 5 on localhost: answers a UDP probe with the 276-byte packet, and POST /detail with `reply`.
    Returns (probe address, what was asked for, stop)."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    asked = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            asked.append((self.path, body))
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "appliation/json")      # the firmware's own spelling
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    web = HTTPServer(("127.0.0.1", 0), Handler)
    packet = bytearray(276)
    packet[0:9] = b"Workshop\0"
    packet[0x88:0x8A] = pid.to_bytes(2, "big")
    packet[0x8E:0x90] = web.server_address[1].to_bytes(2, "big")
    packet[0x90], packet[0x91] = mode, 1 - (mode == 0)
    packet[0x92:0x92 + 10] = b"SNTEST0001"
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)
    running = threading.Event()
    running.set()

    def answer():
        while running.is_set():
            try:
                _, sender = udp.recvfrom(64)
                udp.sendto(bytes(packet), sender)
            except OSError:
                pass

    threads = [threading.Thread(target=answer, daemon=True), threading.Thread(target=web.serve_forever, daemon=True)]
    for thread in threads:
        thread.start()

    def stop():
        running.clear()
        web.shutdown()
        web.server_close()
        threads[0].join(1)
        udp.close()

    return (udp.getsockname(),), asked, stop


def test_printer_status_is_read_only_and_says_what_to_do_when_it_cannot_read(monkeypatch):
    from creator5_mcp import printer
    detail = {"status": "printing", "firmwareVersion": "1.9.8", "printFileName": "hook.3mf", "printProgress": 0.42,
              "printLayer": 120, "targetPrintLayer": 391, "printDuration": 2400, "estimatedTime": 3500,
              "nozzleTemps": [210.2, 24.0, 24.1, 23.9], "nozzleTargetTemps": [210, 0, 0, 0], "platTemp": 59.8, "platTargetTemp": 60,
              "flashRegisterCode": "secret-register-code",
              "matlStationInfo": {"currentSlot": 1, "slotInfos": [{"slotId": 1, "hasFilament": True, "materialName": "PLA",
                                                                   "materialColor": "#161616"}]}}
    probes, asked, stop = _fake_printer({"code": 0, "message": "Success", "detail": detail})
    try:
        seen = printer.discover(0.5, probes)
        assert [(p["name"], p["serial_number"], p["model"], p["mode"], p["address"]) for p in seen] == \
               [("Workshop", "SNTEST0001", "Creator 5", "LAN-only", "127.0.0.1")]
        # No check code stored: found, not read, and told how to store it, in a command with no quotation marks.
        none = printer.status(0.5, probes, code_for=lambda serial: None)
        assert none["found"] and not none["status_read"] and asked == []
        assert "security add-generic-password -s creator5-slicer-mcp-printer -a SNTEST0001 -w" in none["message"]
        assert chr(34) not in none["message"] and chr(39) not in none["message"].replace("printer's", "")
        # With the code: one question, to /detail, carrying the serial number and code; the state comes back.
        read = printer.status(0.5, probes, code_for=lambda serial: "abcd1234")
        assert asked == [("/detail", {"serialNumber": "SNTEST0001", "checkCode": "abcd1234"})]
        assert read["status_read"] and read["state"] == "printing" and read["progress"] == 0.42 and read["layers"] == 391
        assert read["nozzle_c"][0] == 210.2 and read["bed_target_c"] == 60 and read["toolhead_in_use"] == 1
        assert read["loaded"] == [{"toolhead": 1, "has_filament": True, "material": "PLA", "colour": "#161616"}]
        # Neither the check code nor the printer's cloud register code is ever handed back.
        assert "abcd1234" not in json.dumps(read) and "secret-register-code" not in json.dumps(read)
        monkeypatch.setattr(printer, "status", lambda: {"found": False})    # the tool itself never touches a real network in tests
        assert server.printer_status() == {"ok": True, "found": False}
        monkeypatch.undo()
    finally:
        stop()
    # Cloud mode and no code stored: the probe's own answer is enough to say why status cannot be read, with nothing more sent.
    probes, asked, stop = _fake_printer({"code": -2, "message": "Lan mode error"}, mode=1)
    try:
        unasked = printer.status(0.5, probes, code_for=lambda serial: None)
        assert unasked["mode"] == "cloud" and asked == [] and unasked["message"].startswith("The printer was found, and it is in cloud mode")
        assert "turns the Flashforge cloud off" in unasked["message"] and "security add-generic-password" in unasked["message"]
    finally:
        stop()
    # Cloud mode: the printer refuses with code -2, and the answer says what LAN-only mode costs.
    probes, asked, stop = _fake_printer({"code": -2, "message": "Lan mode error"}, mode=1, pid=41)
    try:
        cloud = printer.status(0.5, probes, code_for=lambda serial: "abcd1234")
        assert cloud["found"] and cloud["model"] == "Creator 5 Pro" and cloud["mode"] == "cloud" and not cloud["status_read"]
        assert "cloud mode" in cloud["message"] and "turns the Flashforge cloud off" in cloud["message"]
    finally:
        stop()
    probes, asked, stop = _fake_printer({"code": 1, "message": "Access code is different"})
    try:
        wrong = printer.status(0.5, probes, code_for=lambda serial: "nope")
        assert "Access code is different" in wrong["message"] and "security delete-generic-password" in wrong["message"]
    finally:
        stop()
    # Nothing on the network: an answer, not an error.
    silent = printer.status(0.3, (("127.0.0.1", 9),), code_for=lambda serial: "abcd1234")
    assert silent["found"] is False and "No Creator 5 answered" in silent["message"]
    # Read-only by construction: the module names one endpoint, and none that changes the printer.
    source = Path(printer.__file__).read_text()
    assert printer.ENDPOINT == "/detail" and source.count("ENDPOINT") == 2
    for forbidden in ("/control", "/printGcode", "/uploadGcode", "/gcodeList", "/gcodeThumb", "/notifyWanBind", "/product", "/checkCode"):
        assert forbidden not in source, forbidden


def test_print_risks_name_the_parts_likely_to_topple_or_come_off(fake_env, cube_stl, tmp_path):
    from conftest import _stl, box_triangles
    from creator5_mcp import recommend
    post = _stl(tmp_path / "post.stl", box_triangles(8, 8, 60))
    bare = server.slice_model(str(post), "PLA", brim="off")
    assert bare["print_risks"] and "'post' is tall and narrow: 60 mm high on a footing 8 mm wide" in bare["print_risks"][0]
    assert "Turn the brim on" in bare["print_risks"][0] and any(w.startswith("Print risk: 'post'") for w in bare["plan"]["warnings"])
    assert "A brim is on" in server.slice_model(str(post), "PLA", brim="on")["print_risks"][0]
    # A low wide part says nothing; a small one with no brim may come off; with a brim it is left alone.
    assert server.slice_model(str(cube_stl), "PLA", brim="off")["print_risks"] == []
    peg = _stl(tmp_path / "peg.stl", box_triangles(5, 5, 10))
    assert "rests on only 25 mm² of the plate and the brim is off" in server.slice_model(str(peg), "PLA", brim="off")["print_risks"][0]
    assert server.slice_model(str(peg), "PLA", brim="on")["print_risks"] == []
    # A filament with no temperature for the plate in use.
    cold = server.slice_model(str(cube_stl), "PLA", filament_overrides={"textured_plate_temp": 0})
    assert any("toolhead 1 has no temperature for the textured plate" in risk for risk in cold["print_risks"])
    # Four tall parts in a long print, and the same four in a short one.
    shapes = [{"name": f"tower {i}", "height_mm": 80.0, "narrow_side_mm": 30.0, "bed_contact_mm2": 900.0} for i in range(4)]
    assert "4 parts up to 80 mm tall share one 5.0 hour print" in recommend.print_risks(shapes, {"brim_type": "outer_only"}, 18000)[0]
    assert recommend.print_risks(shapes, {"brim_type": "outer_only"}, 3600) == []


def test_a_rib_the_gcode_leaves_out_is_found_at_a_third_of_a_millimetre(fake_env, tmp_path):
    from conftest import _stl, box_triangles
    from creator5_mcp import model, slicecheck
    rib = [[(x + 20, y + 5, z) for x, y, z in tri] for tri in box_triangles(10, 0.3, 4)]       # a 0.3 mm rib beside a block
    project = Path(server.slice_model(str(_stl(tmp_path / "ribbed.stl", box_triangles(20, 10, 4) + rib)), "PLA")["project_3mf"])
    points = model.load_objects(project)["objects"][0]["parts"][0]["tris"].reshape(-1, 3)
    x0, y0 = points[:, 0].min(), points[:, 1].min()
    block = _ring(x0 + 0.2, y0 + 0.2, x0 + 19.8, y0 + 9.8)
    line = f"G1 X{x0 + 20} Y{y0 + 5.15}\nG1 X{x0 + 30} Y{y0 + 5.15} E1\n"
    with_rib = slicecheck.check(project, _gcode(tmp_path / "with.gcode", [[("Outer wall", 0, block + line)]] * 20))
    without = slicecheck.check(project, _gcode(tmp_path / "without.gcode", [[("Outer wall", 0, block)]] * 20))
    assert with_rib["smaller"] == [] and with_rib["warnings"] == []
    assert without["smaller"] and 1.5 < without["smaller"][0]["missing_mm2"] < 4 and "prints smaller than its model" in without["warnings"][0]


def test_painted_triangles_are_split_as_the_slicer_splits_them_and_small_parts_are_framed(fake_env, cube_stl):
    import numpy as np
    from creator5_mcp import preview
    tri = np.array([[0.0, 0, 0], [4, 0, 0], [0, 4, 0]])
    area = lambda t: abs(np.cross(t[1] - t[0], t[2] - t[0])[2]) / 2
    # Whole triangles: toolheads 1 to 4, and unpainted, which keeps the part's own.
    assert [preview._painted(tri, code, 7)[0][1] for code in ("4", "8", "0C", "1C", "0")] == [1, 2, 3, 4, 7]
    # Split on all three sides into four pieces, written last piece first: corners 1, 2, 1 and the middle 2.
    four = preview._painted(tri, "48483", 7)
    assert [slot for _, slot in four] == [2, 1, 2, 1] and [area(t) for t, _ in four] == [2.0] * 4
    assert sorted(tuple(np.round(t.mean(axis=0)[:2], 3)) for t, _ in four)[0] == (0.667, 0.667)       # the corner piece at the origin
    # One side split in two, the half nearer the first corner painted 2 and the other left to the part.
    two = preview._painted(tri, "085", 7)
    assert sum(area(t) for t, _ in two) == 8.0 and sorted(slot for _, slot in two) == [2, 7]
    # A piece split again: three pieces, and together they still cover the whole triangle.
    nested = preview._painted(tri, "48141", 7)
    assert len(nested) == 3 and sum(area(t) for t, _ in nested) == 8.0 and sorted(slot for _, slot in nested) == [1, 1, 2]
    # A 20 mm cube is a few pixels on the whole bed; framed, it fills the picture.
    project = Path(server.slice_model(str(cube_stl), "red PLA")["project_3mf"])
    red = lambda png: int(((_pixels(png)[:, :, 0] > 120) & (_pixels(png)[:, :, 1] < 80)).sum())
    assert preview.fills_little(project)
    whole, framed = red(preview.plate_png(project, "top")[0]), red(preview.plate_png(project, "top", zoom=True)[0])
    assert 3500 < whole < 5000 and framed > 400_000


def test_a_plate_is_chosen_and_the_designers_settings_are_accounted_for(fake_env, tmp_path):
    from conftest import box_triangles, make_3mf
    designed = {"printer_settings_id": "Bambu Lab A1 0.4 nozzle", "print_settings_id": "0.20mm Standard @BBL A1",
                "filament_settings_id": ["Bambu PLA Basic @BBL A1"], "enable_support": "1", "support_type": "tree(auto)",
                "brim_type": "outer_only", "brim_width": "3", "wall_loops": "6",
                "different_settings_to_system": ["enable_support;support_type;wall_loops;ironing_type", "", ""]}
    path = make_3mf(tmp_path / "two_plates.3mf", box_triangles(20, 10, 5), designed)
    first = server.slice_model(str(path), "PLA")
    assert first["ok"] and first["model"]["plates"] == 2 and first["objects"] == ["holder v3"]
    assert any("holds 2 plates; plate 1 was used" in note for note in first["model"]["notes"])
    # The second plate holds the other object, and only it.
    second = server.slice_model(str(path), "PLA", plate=2)
    assert second["ok"] and second["objects"] == ["two_plates"] and second["model"]["size_mm"] == [20.0, 10.0, 5.0]
    assert "there is no plate 3" in server.slice_model(str(path), "PLA", plate=3)["error"]
    # What became of the designer's settings: supports kept, the brim width kept, the rest named as left behind.
    report = first["designer_report"]
    assert report["made_for"]["printer"] == "Bambu Lab A1 0.4 nozzle" and report["kept"]["enable_support"] == "1"
    assert report["kept"]["brim_width"] == "3" and report["left_behind"] == ["ironing_type", "wall_loops"]
    assert any("2 settings its designer changed are not carried over" in note for note in first["notes"])
    assert server.slice_model(str(tmp_path / "two_plates.3mf"), "PLA", supports="off")["designer_report"]["replaced"]["enable_support"] == \
        {"designer": "1", "used": "0"}


def test_failed_parts_are_reprinted_alone_and_two_slices_are_compared(fake_env, cube_stl, labelled_3mf):
    import zipfile
    plate = server.slice_model(str(labelled_3mf), "white PLA", other_filaments=["black PLA"], copies=3,
                               process_overrides={"wall_loops": 5}, pause_before_z=3.0)
    assert plate["ok"], plate                                  # three copies: three objects in the project
    # A failed plate of several offers the way to reprint some of them.
    logged = server.record_print_result(plate["project_3mf"], "failed", "the second tag came off")
    assert "reprint_objects" in logged["reprint"] and "2 = KeyTag_TwoColour" in logged["reprint"]
    again = server.reprint_objects(plate["project_3mf"], [2])
    assert again["ok"] and again["objects"] == ["KeyTag_TwoColour"] and again["settings_mismatched"] == [], again
    with zipfile.ZipFile(plate["project_3mf"]) as old, zipfile.ZipFile(again["project_3mf"]) as new:
        before, after = (json.loads(z.read("Metadata/project_settings.config")) for z in (old, new))
        assert new.read("Metadata/custom_gcode_per_layer.xml") == old.read("Metadata/custom_gcode_per_layer.xml")     # the pause is kept
        assert new.read("3D/3dmodel.model").count(b"<item objectid") == 1
    # Same presets and changes, both toolheads, and a brim added and marked as changed.
    same = [k for k in before if k not in ("brim_type", "brim_width", "different_settings_to_system")]
    assert all(before[k] == after[k] for k in same) and after["wall_loops"] == "5" and len(after["filament_settings_id"]) == 2
    assert (after["brim_type"], after["brim_width"]) == ("outer_only", "5")
    assert {"brim_type", "brim_width", "wall_loops"} <= set(after["different_settings_to_system"][0].split(";"))
    assert Path(again["plate_png"]).is_file()
    assert "choose from 1 to 3" in server.reprint_objects(plate["project_3mf"], [4])["error"]
    assert "reprint" not in server.record_print_result(again["project_3mf"], "failed")       # one object: nothing to choose
    # Standard against functional: the settings that differ are listed with both values.
    compared = server.compare_settings(str(cube_stl), "PLA")
    assert compared["ok"] and compared["a"]["strength"] == "standard" and compared["b"]["strength"] == "functional"
    assert compared["settings_that_differ"]["wall_loops"] == {"a": "3", "b": "4"} or "wall_loops" in compared["settings_that_differ"]
    assert "sparse_infill_density" in compared["settings_that_differ"] and compared["a"]["project_3mf"] != compared["b"]["project_3mf"]
    assert set(compared["b_against_a"]) == {"seconds", "minutes", "filament_g", "layers"}
    assert server.compare_settings(str(cube_stl), "nonsense filament")["ok"] is False


def test_a_flow_test_is_a_plate_of_tubes_and_its_result_becomes_the_spools_limit(fake_env, cube_stl):
    import zipfile
    from creator5_mcp import flowtest
    server.save_spool("black pla", "Creality Ender PLA", nozzle_temp=215, bed_temp=60, max_speed=80, colour="black")
    made = server.flow_test("black pla")
    assert made["ok"], made
    assert [t["flow_mm3_s"] for t in made["tubes"]] == [5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 22.5, 25.0]
    with zipfile.ZipFile(made["project_3mf"]) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        objects = archive.read("Metadata/model_settings.config").decode()
    # The recipe: one wall of a wide thick line, nothing inside, the flow cap lifted, the spool's own temperature kept.
    assert (settings["wall_loops"], settings["sparse_infill_density"], settings["top_shell_layers"]) == ("1", "0%", "0")
    assert (settings["outer_wall_line_width"], settings["layer_height"]) == ("0.7", "0.28")
    assert settings["filament_max_volumetric_speed"] == ["200"] and settings["nozzle_temperature"] == ["215"]
    # Each tube carries its own wall speed: flow over the line's cross-section, 0.7 x 0.28 mm.
    assert objects.count('key="outer_wall_speed"') == 9
    assert 'value="Flow 5"' in objects and 'value="25.5"' in objects and 'value="Flow 25"' in objects and 'value="127.6"' in objects
    # The fake slicer prints no tubes, so the test is refused rather than trusted.
    assert all(t["flow_in_gcode"] is None for t in made["tubes"]) and any("Do not print it" in w for w in made["warnings"])
    # The fifth tube was the last good one: 15 mm3/s becomes the spool's limit, and its label figures are kept.
    saved = server.record_flow_test("black pla", made["project_3mf"], 5)
    assert saved["ok"] and saved["flow_limit_mm3_s"] == 15.0 and saved["spool"]["max_flow"] == 15.0 and saved["spool"]["nozzle_temp"] == 215
    assert server.record_flow_test("black pla", made["project_3mf"], 9)["note"].startswith("Every tube was good")
    assert "tubes 1 to 9" in server.record_flow_test("black pla", made["project_3mf"], 10)["error"]
    assert "No spool called" in server.record_flow_test("mystery", made["project_3mf"], 3)["error"]
    assert server.record_flow_test("new petg", made["project_3mf"], 3, filament="PETG")["spool"]["max_flow"] == 10.0
    # Too many tubes for the bed, and a project that is not a flow test.
    assert "15 fit the bed" in server.flow_test("PLA", start=2, end=40, step=1)["error"]
    assert "not a flow test" in server.record_flow_test("black pla", server.slice_model(str(cube_stl), "PLA")["project_3mf"], 1)["error"]
    assert flowtest.tube().shape == (200, 3, 3)


def test_doctor_names_each_fault_and_uninstall_leaves_the_prints_alone(fake_env, tmp_path, monkeypatch, capsys):
    from creator5_mcp import doctor
    desktop = tmp_path / "claude_desktop_config.json"
    monkeypatch.setenv("CLAUDE_DESKTOP_CONFIG", str(desktop))
    monkeypatch.setattr(doctor, "skill_folder", lambda: tmp_path / "no-skill-here")
    code = tmp_path / "claude.json"
    # Not connected anywhere, and no print check tool: both are named, each with its fix.
    found = doctor.checks(code)
    said = {text: (ok, fix) for ok, text, fix in found}
    assert any(text.startswith("Slicer found") and ok for text, (ok, _) in said.items())
    assert said["The print check tool (cad) is not installed; shapes are sliced unchecked."] == (False, "Run: uv tool install cad")
    assert said["Neither Claude Desktop nor Claude Code is connected to this server."][1].startswith("Run ./scripts/setup.sh")
    # Claude Desktop pointing at another copy is a fault; pointing at this one is not.
    desktop.write_text(json.dumps({"mcpServers": {"creator5-slicer": {"command": "/old/copy/creator5-slicer-mcp"}, "other": {"command": "x"}},
                                   "theme": "dark"}))
    assert any(not ok and "Claude Desktop runs another copy: /old/copy/creator5-slicer-mcp" in text for ok, text, _ in doctor.checks(code))
    desktop.write_text(json.dumps({"mcpServers": {"creator5-slicer": {"command": str(doctor._here())}, "other": {"command": "x"}}, "theme": "dark"}))
    assert any(ok and text == "Claude Desktop: connected to this copy." for ok, text, _ in doctor.checks(code))
    assert doctor.doctor() == 1                                  # cad is still missing here
    printed = capsys.readouterr().out
    assert "FIX  The print check tool" in printed and chr(34) not in printed and "to fix, marked FIX above" in printed
    # Uninstall removes only this server's entry, keeps a backup, and touches nothing in the prints folder.
    kept = tmp_path / "out" / "print_results.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("[]")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    skill = tmp_path / "skills" / "creator5-slicer"                # never the real one under the home folder
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill")
    monkeypatch.setattr(doctor, "skill_folder", lambda: skill)
    assert any(text == "Skill for Claude: installed." for _, text, _ in doctor.checks(code))
    assert doctor.main(["uninstall"]) == 0
    assert not skill.exists()
    after = json.loads(desktop.read_text())
    assert after == {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"} and kept.read_text() == "[]"
    assert len(list(tmp_path.glob("claude_desktop_config.json.backup-*"))) == 1
    assert "were left alone" in capsys.readouterr().out


def test_the_desktop_bundle_holds_a_valid_manifest_and_the_server(tmp_path):
    import importlib.util
    import zipfile
    spec = importlib.util.spec_from_file_location("build_bundle", Path(server.__file__).parents[2] / "scripts" / "build_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = module.build(tmp_path)
    assert bundle.suffix == ".mcpb" and bundle.stat().st_size < 400_000
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        packed = archive.read("pyproject.toml").decode()
    # The fields the manifest spec requires, and the uv server type, which needs pyproject.toml and no packed libraries.
    assert {"manifest_version", "name", "version", "description", "author", "server"} <= set(manifest) and manifest["author"]["name"]
    assert manifest["server"]["type"] == "uv" and manifest["server"]["entry_point"] in names and manifest["manifest_version"] == "0.4"
    assert manifest["server"]["mcp_config"]["args"][-1] == "creator5-slicer-mcp" and 'creator5-slicer-mcp = "creator5_mcp.server:main"' in packed
    assert manifest["version"] in bundle.name and {"pyproject.toml", "LICENSE", "THIRD_PARTY_NOTICES.md"} <= names
    for module_name in ("server", "printcheck", "slicecheck", "preview", "printer", "flowtest", "doctor"):
        assert f"src/creator5_mcp/{module_name}.py" in names
    assert not any(".venv" in name or "__pycache__" in name or name.startswith("tests/") for name in names)


def test_temperature_and_pressure_advance_towers_and_what_they_save(fake_env, cube_stl):
    import zipfile
    from creator5_mcp import towers
    server.save_spool("white petg", "PETG", nozzle_temp=255, bed_temp=70, colour="white")
    hot = server.temperature_test("white petg", hottest=260, coolest=230, step=10)
    assert hot["ok"], hot
    assert [(b["block"], b["value"], b["from_mm"], b["to_mm"]) for b in hot["blocks"]] == \
        [(1, 260, 0.6, 8.6), (2, 250, 8.6, 16.6), (3, 240, 16.6, 24.6), (4, 230, 24.6, 32.6)]
    with zipfile.ZipFile(hot["project_3mf"]) as archive:
        at_heights = archive.read("Metadata/custom_gcode_per_layer.xml").decode()
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    # The first block's temperature is the filament's own; each later block changes it at its first layer, in the
    # attribute Flash Studio reads custom G-code from.
    assert settings["nozzle_temperature"] == ["260"] and settings["layer_height"] == "0.2"
    assert at_heights.count('type="4"') == 3 and 'top_z="8.8" type="4" extruder="1" color="" extra="M104 S250 ;' in at_heights
    assert 'top_z="24.8"' in at_heights and "M104 S230" in at_heights
    # The fake slicer writes none of it into its G-code, so the tower is refused rather than trusted.
    assert any("Do not print it" in w for w in hot["warnings"])
    # Block 3 was best: 240 C goes to the spool, and its bed temperature stays.
    saved = server.record_temperature_test("white petg", hot["project_3mf"], 3)
    assert saved["ok"] and saved["nozzle_temp"] == 240 and saved["replaced"] == 255 and saved["spool"]["bed_temp"] == 70
    assert "end of the tower" in server.record_temperature_test("white petg", hot["project_3mf"], 4)["note"]
    assert "blocks 1 to 4" in server.record_temperature_test("white petg", hot["project_3mf"], 5)["error"]
    # With no range given the filament preset's own range is used; these test presets carry none, so it is asked for.
    assert "gives no temperature range" in server.temperature_test("PLA")["error"]
    assert "hottest temperature first" in server.temperature_test("PLA", hottest=200, coolest=220)["error"]
    # Pressure advance rises up a tube; the chosen value is used by every later slice with that spool.
    tube = server.pressure_advance_test("white petg", start=0.02, end=0.08, step=0.02)
    assert tube["ok"] and [b["value"] for b in tube["blocks"]] == [0.02, 0.04, 0.06, 0.08]
    with zipfile.ZipFile(tube["project_3mf"]) as archive:
        at_heights = archive.read("Metadata/custom_gcode_per_layer.xml").decode()
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    one = lambda value: value[0] if isinstance(value, list) else value      # these test presets do not list the setting per toolhead
    assert one(settings["pressure_advance"]) == "0.02" and one(settings["enable_pressure_advance"]) == "1" and settings["wall_loops"] == "2"
    assert 'top_z="4.2"' in at_heights and "SET_PRESSURE_ADVANCE ADVANCE=0.04" in at_heights and at_heights.count('type="4"') == 3
    kept = server.record_pressure_advance_test("white petg", tube["project_3mf"], 3)
    assert kept["ok"] and kept["pressure_advance"] == 0.06
    later = server.slice_model(str(cube_stl), "white petg")
    with zipfile.ZipFile(later["project_3mf"]) as archive:
        used = json.loads(archive.read("Metadata/project_settings.config"))
    assert one(used["pressure_advance"]) == "0.06" and used["nozzle_temperature"] == ["230"]      # the last block recorded above
    assert any("Pressure advance 0.06, from this spool's own test" in line for line in later["plan"]["rationale"])
    assert "pressure_advance" in used["different_settings_to_system"][1]
    # A tower of the other kind, and a spool nobody has saved.
    assert "not a temperature test" in server.record_temperature_test("white petg", tube["project_3mf"], 1)["error"]
    assert "No spool called" in server.record_pressure_advance_test("mystery", tube["project_3mf"], 2)["error"]
    assert server.record_pressure_advance_test("grey petg", tube["project_3mf"], 2, filament="PETG")["spool"]["pressure_advance"] == 0.04
    # The reader gives each command the layer it runs in.
    gcode = fake_env / "t.gcode"
    gcode.write_text("M104 S200\n;LAYER_CHANGE\n;Z:0.2\nG1 X1\n;LAYER_CHANGE\n;Z:8.8\n;CUSTOM_GCODE\nM104 S250 ; block 2\nG1 X2\n")
    assert towers.commands(gcode, "M104") == [(8.8, "M104 S250")]


def test_the_skill_names_every_tool_and_no_tool_the_server_lacks():
    import re
    skill = (Path(server.__file__).parents[2] / "skills" / "creator5-slicer" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: creator5-slicer\ndescription: ")
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert len(tools) == 20
    assert tools - {name for name in tools if f"`{name}`" in skill} == set()          # every tool is named
    # Anything written like a tool call is a real tool: a name in backticks that ends a table row's tool column.
    named = set(re.findall(r"\| `([a-z_]+)`", skill)) | set(re.findall(r"then `([a-z_]+)`", skill))
    assert named and named <= tools, named - tools
    assert chr(8220) not in skill and chr(8221) not in skill
