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
    # A plain box needs no support: the setting is on, and the real slicer prints none.
    assert result["supports_on"] and result["support_sections_in_gcode"] == 0


def test_best_quality_uses_the_thinner_creator_5_preset(real_env):
    from creator5_mcp import recommend
    profiles_dir = FLASH_STUDIO.parents[1] / "Resources" / "profiles"
    assert recommend.recommend(profiles_dir, "PLA", quality="best")["process_preset"] == "0.10mm Standard @FF C5"
    assert recommend.recommend(profiles_dir, "PLA", quality="fine")["process_preset"] == "0.12mm Standard @FF C5"


def test_multi_colour_project_prints_each_part_with_its_own_toolhead(real_env, colour_3mf):
    from creator5_mcp import server
    result = server.slice_model(str(colour_3mf), "black PLA", other_filaments=["white PLA", "gold silk PLA"])
    assert result["ok"], result
    assert result["exit_code"] == 0 and result["settings_mismatched"] == []
    gcode = Path(result["gcode"]).read_text(errors="ignore")
    # Flash Studio named both objects from the project and used all three toolheads.
    assert "EXCLUDE_OBJECT_DEFINE NAME=Figure" in gcode and "EXCLUDE_OBJECT_DEFINE NAME=Plinth" in gcode
    assert '; filament_settings_id = "Flashforge PLA Basic @FF C5";"Flashforge PLA Basic @FF C5";"Flashforge PLA Silk @FF C5"' in gcode
    assert "; filament_colour = #161616;#F2F2F2;#D4AF37" in gcode
    used = next(line for line in gcode.splitlines() if line.startswith("; filament used [g]"))
    grams = [float(v) for v in used.split("=")[1].split(",")]
    assert len(grams) == 3 and all(g > 0 for g in grams), used


def test_each_toolhead_keeps_its_own_filament_settings(real_env, colour_3mf):
    """Toolhead 1 has label figures; toolheads 2 and 3 must keep their own presets' values, not copy them."""
    from creator5_mcp import server
    result = server.slice_model(str(colour_3mf), "ender pla", other_filaments=["PLA", "PETG"])
    assert result["ok"], result
    assert result["settings_mismatched"] == [], result["settings_mismatched"]
    gcode = Path(result["gcode"]).read_text(errors="ignore")
    assert "; nozzle_temperature = 215,210,255" in gcode
    # The real presets differ in bed temperature, the slicer heats to the highest, and the plan says so.
    assert "M140 S85" in gcode and "M140 S60" not in gcode
    assert any("bed will run at 85 C" in w for w in result["plan"]["warnings"])
    assert "; filament_max_volumetric_speed = 6.7," in gcode and "; filament_max_volumetric_speed = 6.7,6.7" not in gcode


def test_step_file_is_meshed_then_oriented_and_sliced_by_flash_studio(real_env):
    pytest.importorskip("cascadio")
    from conftest import write_step_box
    from creator5_mcp import server
    # Stood on its smallest face: Flash Studio's orienter should lay it flat.
    result = server.slice_model(str(write_step_box(real_env / "plate.step", 12, 40, 60)), "PLA", auto_orient=True)
    assert result["ok"], result
    assert result["exit_code"] == 0 and result["settings_mismatched"] == []
    assert sorted(result["model"]["size_mm"]) == [12.0, 40.0, 60.0] and result["model"]["size_mm"][2] == 12.0
    assert "EXCLUDE_OBJECT_DEFINE NAME=plate" in Path(result["gcode"]).read_text(errors="ignore")


def test_pause_lands_once_at_the_start_of_the_layer_asked_for(real_env, labelled_3mf):
    """A two-colour part with an embedded-tag pause: Flash Studio pauses once, before the layer at 3.0 mm."""
    from creator5_mcp import server
    result = server.slice_model(str(labelled_3mf), "PLA", other_filaments=["PLA"], pause_before_z=3.0,
                                process_overrides={"initial_layer_print_height": "0.2"})
    assert result["ok"], result
    assert result["pauses_at_z"] == [3.0] and result["settings_mismatched"] == []
    lines = Path(result["gcode"]).read_text(errors="ignore").splitlines()
    assert lines.count("M25") == 1
    at = lines.index("M25")
    before = [line for line in lines[:at] if line.startswith(";Z:")]
    assert before[-1] == ";Z:3" and len(before) == 15            # 14 layers done, the 15th just begun
    assert not any(line.startswith(("G1", "G2", "G3")) and " E" in line and "E-" not in line
                   for line in lines[at - 6:at])                 # nothing extruded between the layer change and the pause
    # The inlay is 0.6 mm tall, so toolhead 2 is used in the first three layers only.
    layer, used = 0, set()
    for line in lines:
        if line.startswith(";Z:"):
            layer += 1
        elif line == "T1":
            used.add(layer)
    assert used and max(used) <= 3, used
