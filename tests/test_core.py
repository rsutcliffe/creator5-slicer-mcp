import json
from pathlib import Path

import pytest

from creator5_mcp import model, profiles, recommend


def test_flatten_merges_chain_across_vendors(profiles_dir):
    flat = profiles.flatten(profiles_dir, "filament", "Flashforge PLA Basic @FF C5")
    assert flat["nozzle_temperature"] == ["210"]       # child wins
    assert flat["filament_density"] == ["1.24"]        # inherited from the shared library
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
    ("metallic silver PETG", "PETG"),
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


def test_shape_drives_supports_and_brim(profiles_dir, cube_stl, mushroom_stl):
    flat = recommend.recommend(profiles_dir, "PLA", analysis=model.analyse(cube_stl))["process_overrides"]
    assert flat["enable_support"] == "0" and flat["brim_type"] == "no_brim"
    tall = recommend.recommend(profiles_dir, "PLA", analysis=model.analyse(mushroom_stl))["process_overrides"]
    assert tall["enable_support"] == "1" and tall["support_type"] == "tree(auto)"
    assert tall["brim_type"] == "outer_only" and tall["brim_width"] == "5"


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
                 "PPS_CF": "pps-cf", "PA_CF": "pa-cf"}.get(material.key, material.key.lower())
        assert recommend.match_material(probe).key == material.key, probe
        for nozzle in ("0.4", "0.6", "0.8"):
            plan = recommend.recommend(real_profiles, probe, nozzle=nozzle, quality="fine", strength="structural")
            process = profiles.flatten(real_profiles, "process", plan["process_preset"])
            filament = profiles.flatten(real_profiles, "filament", plan["filament_preset"])
            assert plan["machine_preset"] in process["compatible_printers"]
            assert plan["machine_preset"] in filament["compatible_printers"]
            assert plan["process_overrides"]["sparse_infill_pattern"] in valid_patterns
            # Every key we set must already exist in the real base preset, apart from the one known addition.
            _, unknown = profiles.apply_overrides(process, plan["process_overrides"])
            assert set(unknown) <= {"wall_sequence"}, unknown
