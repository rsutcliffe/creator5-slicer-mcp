import json
from pathlib import Path

import pytest

from creator5_mcp import model, profiles, recommend


def test_flatten_merges_chain_across_vendors(profiles_dir):
    flat = profiles.flatten(profiles_dir, "filament", "Flashforge PLA Basic @FF C5")
    assert flat["nozzle_temperature"] == ["210"]       # child wins
    assert flat["filament_density"] == ["1.24"]        # inherited from the shared library
    # The library preset's parent comes from the library, not the same-named Flashforge file.
    assert flat["filament_diameter"] == ["1.75"]
    assert "inherits" not in flat and flat["from"] == "system"


def test_flatten_reports_missing_parent(profiles_dir):
    with pytest.raises(profiles.ProfileError, match="not found"):
        profiles.flatten(profiles_dir, "process", "does not exist")


def test_apply_overrides_matches_shape_and_flags_unknown_keys():
    merged, unknown = profiles.apply_overrides({"a": "1", "temps": ["200", "200"]},
                                               {"a": 5, "temps": 215, "flag": True, "typo_key": "x"})
    assert merged["a"] == "5" and merged["temps"] == ["215", "215"] and merged["flag"] == "1"
    assert unknown == ["flag", "typo_key"]


def test_analyse_cube(cube_stl):
    a = model.analyse(cube_stl)
    assert a["size_mm"] == [20.0, 20.0, 10.0]
    assert a["volume_cm3"] == 4.0
    assert a["bed_contact_area_mm2"] == 400.0
    assert a["overhang_fraction"] == 0 and a["fits_bed"]


def test_analyse_finds_overhang_and_tall_shape(mushroom_stl):
    a = model.analyse(mushroom_stl)
    assert a["overhang_fraction"] > 0.1
    assert a["bed_contact_area_mm2"] == 25.0


@pytest.mark.parametrize("text,key", [
    ("black PLA", "PLA"), ("PETG", "PETG"), ("silk gold pla", "PLA_SILK"), ("TPU 95A", "TPU"),
    ("tpu 90a", "TPU_90A"), ("ABS", "ABS"), ("PLA-CF", "PLA_CF"), ("petg-cf", "PETG_CF"),
    ("nylon", "PA_CF"), ("ASA", "ASA"), ("PLA+", "PLA_PRO"), ("matte black PLA", "PLA_MATTE"),
    ("metallic silver PETG", "PETG"), ("Creality Ender black PLA", "PLA_ENDER"), ("ender pla", "PLA_ENDER"),
])
def test_match_material(text, key):
    assert recommend.match_material(text).key == key


def test_unknown_material_is_rejected():
    with pytest.raises(recommend.RecommendError, match="not recognised"):
        recommend.match_material("unobtainium")


def test_strength_scales_walls_and_infill(profiles_dir):
    walls = {}
    for level in recommend.STRENGTH:
        plan = recommend.recommend(profiles_dir, "PLA", strength=level)
        walls[level] = (int(plan["process_overrides"]["wall_loops"]), plan["process_overrides"]["sparse_infill_density"])
    assert walls == {"decorative": (2, "10%"), "standard": (3, "15%"), "functional": (4, "25%"), "structural": (6, "40%")}


def test_quality_picks_layer_height(profiles_dir):
    heights = {q: recommend.recommend(profiles_dir, "PLA", quality=q)["layer_height_mm"] for q in recommend.QUALITY}
    assert heights == {"draft": 0.24, "standard": 0.2, "fine": 0.12}
    fine = recommend.recommend(profiles_dir, "PLA", quality="fine")["process_overrides"]
    assert fine["outer_wall_speed"] == "100" and fine["wall_sequence"] == "inner-outer-inner wall"


def test_shell_layers_follow_layer_height(profiles_dir):
    fine = recommend.recommend(profiles_dir, "PLA", quality="fine", strength="functional")["process_overrides"]
    assert fine["top_shell_layers"] == "10" and fine["bottom_shell_layers"] == "9"   # 1.2 mm and 1.0 mm at 0.12 mm


def test_silk_keeps_its_slow_outer_wall_under_fine_quality(profiles_dir):
    plan = recommend.recommend(profiles_dir, "silk PLA", quality="fine")
    assert plan["process_overrides"]["outer_wall_speed"] == "60"
    assert plan["filament_preset"] == "Flashforge PLA Silk @FF C5"


def test_ender_pla_follows_its_label(profiles_dir):
    plan = recommend.recommend(profiles_dir, "Creality Ender PLA, black", strength="functional")
    f = plan["filament_overrides"]
    assert plan["filament_preset"] == "Generic PLA @FF C5"
    assert f["nozzle_temperature"] == "215" and f["hot_plate_temp"] == "60" and f["textured_plate_temp_initial_layer"] == "60"
    assert f["filament_max_volumetric_speed"] == "6.7"          # 80 mm/s x 0.42 mm x 0.2 mm
    # Every key set must exist in the filament preset.
    _, unknown = profiles.apply_overrides(profiles.flatten(profiles_dir, "filament", plan["filament_preset"]), f)
    assert unknown == []


def test_label_figures_override_and_are_validated(profiles_dir):
    f = recommend.recommend(profiles_dir, "PLA", nozzle_temp=205, bed_temp=55, max_speed=60)["filament_overrides"]
    assert f["nozzle_temperature"] == "205" and f["hot_plate_temp"] == "55" and f["filament_max_volumetric_speed"] == "5"
    # A generous speed limit leaves the preset's own tighter flow cap alone.
    assert "filament_max_volumetric_speed" not in recommend.recommend(profiles_dir, "PLA", max_speed=400)["filament_overrides"]
    assert recommend.recommend(profiles_dir, "PLA")["filament_overrides"] == {}
    with pytest.raises(recommend.RecommendError):
        recommend.recommend(profiles_dir, "PLA", nozzle_temp=500)


def test_shape_drives_supports_and_brim(profiles_dir, cube_stl, mushroom_stl):
    flat = recommend.recommend(profiles_dir, "PLA", analysis=model.analyse(cube_stl))["process_overrides"]
    assert flat["enable_support"] == "0" and "brim_type" not in flat     # the preset's own auto brim decides
    tall = recommend.recommend(profiles_dir, "PLA", analysis=model.analyse(mushroom_stl))["process_overrides"]
    assert tall["enable_support"] == "1" and tall["support_type"] == "tree(auto)"
    assert tall["brim_type"] == "outer_only" and tall["brim_width"] == "5"


def test_3mf_reads_components_and_skips_modifiers_and_other_plates(mushroom_3mf):
    a = model.analyse(mushroom_3mf)
    assert a["analysed"] and a["size_mm"] == [45.0, 45.0, 45.0]           # modifier and plate 2 left out
    assert a["bed_contact_area_mm2"] == 25.0 and a["overhang_area_mm2"] > 1000
    assert any("2 plates" in n for n in a["notes"])


def test_obj_is_read(tmp_path):
    obj = tmp_path / "quad.obj"
    obj.write_text("v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\nv 0 0 5\nf 1 2 3 4\nf 1/1 2/2 5/5\n")
    tris, _ = model.load_mesh(obj)
    assert tris.shape == (3, 3, 3) and model.analyse(obj)["size_mm"] == [10.0, 10.0, 5.0]


def test_support_angle_sets_what_counts_as_overhang(tmp_path):
    from conftest import _stl
    # One downward face sloping 40 degrees from the horizontal, 20 mm up.
    import math
    run = 10 / math.tan(math.radians(40))
    floor = ((0, 0, 0), (0, 10, 0), (10, 0, 0))
    wedge = _stl(tmp_path / "w.stl", [floor, ((0, 0, 20), (0, 10, 20), (run, 0, 30))])
    assert model.analyse(wedge, 45)["overhang_area_mm2"] > 0      # flatter than 45: needs support
    assert model.analyse(wedge, 30)["overhang_area_mm2"] == 0     # steeper than 30: prints unsupported


def test_designer_settings_are_read_and_followed(profiles_dir, tmp_path, mushroom_3mf):
    from conftest import make_3mf
    saved = {"enable_support": "0", "support_threshold_angle": "40", "brim_type": "no_brim", "brim_width": ["5"], "layer_height": "0.1"}
    path = make_3mf(tmp_path / "d.3mf", [tri for tri in [((0, 0, 0), (1, 0, 0), (0, 1, 0))]], saved)
    designer = model.designer_settings(path)
    assert designer == {"enable_support": "0", "support_threshold_angle": "40", "brim_type": "no_brim", "brim_width": "5"}
    assert model.designer_settings(mushroom_3mf) == {}
    shape = model.analyse(mushroom_3mf)
    # Designer said no supports: followed, though our own check would add them.
    plan = recommend.recommend(profiles_dir, "PLA", analysis=shape, designer=designer)
    assert plan["process_overrides"]["enable_support"] == "0" and any("designer" in r for r in plan["rationale"])
    # A brim for our own reason (tiny bed contact) still wins over the designer's none.
    assert plan["process_overrides"]["brim_type"] == "outer_only"
    # Designer said supports on, with their angle; an explicit choice still beats both.
    on = recommend.recommend(profiles_dir, "PLA", designer={"enable_support": "1", "support_threshold_angle": "40"})
    assert on["process_overrides"]["enable_support"] == "1" and on["process_overrides"]["support_threshold_angle"] == "40"
    assert recommend.recommend(profiles_dir, "PLA", designer=designer, supports="on")["process_overrides"]["enable_support"] == "1"


def test_supports_follow_overhang_area_not_share_of_surface(profiles_dir):
    """A large part with one 100 mm² overhang still needs support, though it is under 1% of the surface."""
    shape = {"analysed": True, "fits_bed": True, "overhang_area_mm2": 100.0, "overhang_fraction": 0.006,
             "support_angle": 30.0, "height_to_footprint": 1.0, "bed_contact_area_mm2": 500.0, "notes": []}
    assert recommend.recommend(profiles_dir, "PLA", analysis=shape)["process_overrides"]["enable_support"] == "1"


def test_colour_comes_from_the_filament_name():
    from creator5_mcp import project
    assert project.colour_for("Creality Ender PLA, black") == "#161616"
    assert project.colour_for("silk gold pla") == "#D4AF37" and project.colour_for("PETG", "#ABCDEF") == "#ABCDEF"


def test_explicit_choices_beat_auto(profiles_dir, mushroom_stl):
    o = recommend.recommend(profiles_dir, "PLA", analysis=model.analyse(mushroom_stl), supports="off", brim="off")["process_overrides"]
    assert o["enable_support"] == "0" and o["brim_type"] == "no_brim"


def test_warping_material_gets_brim_and_warning(profiles_dir):
    plan = recommend.recommend(profiles_dir, "ABS")
    assert plan["process_overrides"]["brim_width"] == "8"
    assert any("chamber" in w for w in plan["warnings"])


def test_oversize_model_warns(profiles_dir, tmp_path):
    from conftest import _stl, box_triangles
    big = model.analyse(_stl(tmp_path / "big.stl", box_triangles(300, 300, 300)))
    assert any("larger than" in w for w in recommend.recommend(profiles_dir, "PLA", analysis=big)["warnings"])


def test_bad_levels_are_rejected(profiles_dir):
    with pytest.raises(recommend.RecommendError):
        recommend.recommend(profiles_dir, "PLA", quality="ultra")
    with pytest.raises(recommend.RecommendError):
        recommend.recommend(profiles_dir, "PLA", supports="maybe")


def test_every_material_resolves_against_real_profiles(real_profiles):
    """Each material this server knows must map to a preset that exists and flattens."""
    valid_patterns = {"gyroid", "cubic"}
    for material in recommend.MATERIALS:
        probe = {"PLA_SILK": "silk pla", "PLA_CF": "pla-cf", "PLA_MATTE": "matte pla", "PLA_HS": "hs pla", "PLA_PRO": "pla pro",
                 "PLA_SPECIAL": "galaxy pla", "PLA_METAL": "metal pla", "PLA_LUMINOUS": "glow pla", "PETG_CF": "petg-cf",
                 "PET_CF": "pet-cf", "PETG_HS": "hs petg", "PETG_CLEAR": "clear petg", "ABS_GF": "abs-gf", "ASA_CF": "asa-cf",
                 "ASA_GF": "asa-gf", "TPU_64D": "tpu 64d", "TPU_90A": "tpu 90a", "PAHT_CF": "paht-cf", "PPA_CF": "ppa-cf",
                 "PPS_CF": "pps-cf", "PA_CF": "pa-cf", "PLA_ENDER": "ender pla"}.get(material.key, material.key.lower())
        assert recommend.match_material(probe).key == material.key, probe
        for nozzle in ("0.4", "0.6", "0.8"):
            plan = recommend.recommend(real_profiles, probe, nozzle=nozzle, quality="fine", strength="structural")
            process = profiles.flatten(real_profiles, "process", plan["process_preset"])
            filament = profiles.flatten(real_profiles, "filament", plan["filament_preset"])
            assert plan["machine_preset"] in process["compatible_printers"]
            assert plan["machine_preset"] in filament["compatible_printers"]
            assert plan["process_overrides"]["sparse_infill_pattern"] in valid_patterns
            assert filament["filament_diameter"] == ["1.75"] * len(filament["filament_diameter"]), plan["filament_preset"]
            # Every key we set must already exist in the real base preset, apart from the one known addition.
            _, unknown = profiles.apply_overrides(process, plan["process_overrides"])
            assert set(unknown) <= {"wall_sequence"}, unknown
            _, unknown = profiles.apply_overrides(filament, plan["filament_overrides"])
            assert unknown == [], unknown
