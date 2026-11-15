"""Slice with the real Flash Studio. Runs only where it is installed, as on the owner's Mac."""

import json
import zipfile
from pathlib import Path

import pytest

from conftest import box_triangles, make_3mf

FLASH_STUDIO = Path("/Applications/Flash Studio.app/Contents/MacOS/Flash Studio")
pytestmark = pytest.mark.skipif(not FLASH_STUDIO.is_file(), reason="Flash Studio is not installed here")


@pytest.fixture
def real_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCA_SLICER_PATH", str(FLASH_STUDIO))
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(FLASH_STUDIO.parents[1] / "Resources" / "profiles"))
    monkeypatch.setenv("C5_OUTPUT_DIR", str(tmp_path / "out"))
    return tmp_path


def test_designer_3mf_slices_as_a_project_with_every_setting_in_the_gcode(real_env):
    from creator5_mcp import server
    # A Bambu-style 3MF whose designer asked for supports.
    model = make_3mf(real_env / "part.3mf", box_triangles(30, 20, 10), {"enable_support": "1", "support_threshold_angle": "40"})
    result = server.slice_model(str(model), "black ender pla", quality="fine", strength="functional")
    assert result["ok"], result
    assert result["exit_code"] == 0 and result["gcode_complete"]
    assert result["settings_mismatched"] == [] and result["settings_checked_in_gcode"] >= 15
    with zipfile.ZipFile(result["project_3mf"]) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    assert settings["enable_support"] == "1" and settings["support_threshold_angle"] == "40"
    assert settings["filament_settings_id"] == ["Generic PLA @FF C5"]
    gcode = Path(result["gcode"]).read_text(errors="ignore")
    assert "; wall_loops = 4" in gcode and "; nozzle_temperature = 215" in gcode
    assert "; filament_settings_id = \"Generic PLA @FF C5\"\n" in gcode       # one filament, not a designer's four
    assert 15 < result["summary"]["print_time_seconds"] / 60 < 120
