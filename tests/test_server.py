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
    assert job.name.endswith("_My_Part") and Path(result["gcode"]).is_file() and Path(result["project_3mf"]).is_file()
    # The slicer received flattened presets carrying the recommended values.
    process = json.loads(Path(result["presets_written"]["process"]).read_text())
    assert process["wall_loops"] == "4" and process["sparse_infill_density"] == "25%" and "inherits" not in process
    filament = json.loads(Path(result["presets_written"]["filament"]).read_text())
    assert filament["filament_density"] == ["1.24"]
    argv = json.loads((job / "argv.json").read_text())
    assert argv.count(str(cube_stl)) == 2 and argv[argv.index("--orient") + 1] == "0"


def test_overrides_apply_last_and_unknown_keys_are_flagged(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PETG", process_overrides={"wall_loops": 7, "wal_loops": 1},
                                filament_overrides={"nozzle_temperature": 250})
    assert result["ok"]
    process = json.loads(Path(result["presets_written"]["process"]).read_text())
    assert process["wall_loops"] == "7"
    assert json.loads(Path(result["presets_written"]["filament"]).read_text())["nozzle_temperature"] == ["250"]
    assert any("wal_loops" in w for w in result["plan"]["warnings"])


def test_slicer_failure_is_reported_not_raised(fake_env, cube_stl):
    result = server.slice_model(str(cube_stl), "PLA", process_overrides={"wall_loops": 99})
    assert not result["ok"] and "simulated failure" in result["error"] and "code 3" in result["error"]


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
