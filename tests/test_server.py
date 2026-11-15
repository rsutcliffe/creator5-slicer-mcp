import asyncio
import json
import os
import sys
from pathlib import Path

from creator5_mcp import printer, server, slicer
from creator5_mcp.config import load_settings


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
    assert result["model"]["file"] == str(job / "oriented" / "oriented.stl")


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
    assert report["ok"] and report["slicer_version"] == "FakeSlicer 9.9.9" and not report["printer_configured"]
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


def test_printer_tools_need_credentials(fake_env, cube_stl):
    assert "C5_PRINTER_IP" in server.printer_status()["error"]
    assert "not configured" in server.send_to_printer(str(cube_stl))["error"]


def test_upload_sends_expected_request(fake_env, tmp_path, monkeypatch):
    import http.server, threading
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"], seen["headers"] = self.path, dict(self.headers)
            seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            payload = b'{"code":0,"message":"Success"}'
            self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(printer, "PORT", httpd.server_address[1])
    monkeypatch.setenv("C5_PRINTER_IP", "127.0.0.1")
    monkeypatch.setenv("C5_PRINTER_SERIAL", "SN123")
    monkeypatch.setenv("C5_PRINTER_CHECK_CODE", "abcd")
    gcode = tmp_path / "part one.gcode"
    gcode.write_text("G1 X1\n")
    try:
        result = server.send_to_printer(str(gcode))
    finally:
        httpd.shutdown()
    assert result["ok"] and result["print_started"] is False
    assert seen["path"] == "/uploadGcode"
    assert seen["headers"]["serialNumber"] == "SN123" and seen["headers"]["printNow"] == "false"
    assert seen["headers"]["fileSize"] == "6" and b'filename="part_one.gcode"' in seen["body"] and b"G1 X1" in seen["body"]


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
                     "open_in_slicer", "printer_status", "send_to_printer"}
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
    assert "1h 23m" in out and "15.25 g" in out
    assert cli.main(["/nope.stl", "--filament", "PLA"]) == 1 and "FAILED" in capsys.readouterr().out
    assert cli.main([str(cube_stl), "--smooth-top"]) == 0 and "ironing_type           top" in capsys.readouterr().out


def test_cli_copies_gcode_and_reports_missing_usb(fake_env, cube_stl, tmp_path, capsys, monkeypatch):
    from creator5_mcp import cli
    dest = tmp_path / "stick"; dest.mkdir()
    assert cli.main([str(cube_stl), "--copies", "2", "--copy-to", str(dest)]) == 0
    assert (dest / "cube_x2.gcode").read_text().endswith("; EXECUTABLE_BLOCK_END\n")
    monkeypatch.setattr(cli, "_usb_volumes", lambda: [])
    assert cli.main([str(cube_stl), "--usb"]) == 1 and "No USB stick found" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_usb_volumes", lambda: [dest])
    assert cli.main([str(cube_stl), "--usb"]) == 0 and (dest / "cube.gcode").is_file()


def test_cli_saves_printer_and_sends(fake_env, cube_stl, capsys, monkeypatch):
    """Printer details are saved once to a file, then used for uploads."""
    import http.server, threading
    from creator5_mcp import cli
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, self.headers.get("printNow"), self.headers.get("checkCode")))
            payload = b'{"code":0,"detail":{"status":"ready"}}'
            self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(printer, "PORT", httpd.server_address[1])
    try:
        assert cli.main([str(cube_stl), "--send"]) == 1 and "not configured" in capsys.readouterr().out.lower()
        assert cli.main(["--save-printer", "127.0.0.1", "SN9", "code9"]) == 0
        assert cli.main([str(cube_stl), "--send"]) == 0
        assert cli.main([str(cube_stl), "--print"]) == 0
    finally:
        httpd.shutdown()
    assert seen == [("/detail", None, None), ("/uploadGcode", "false", "code9"), ("/uploadGcode", "true", "code9")]
