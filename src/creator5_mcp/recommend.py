"""Turn intent (filament, quality, strength, part shape) into slicer settings.

The filament presets shipped with the slicer already carry temperatures, fan
curves, flow limits and retraction for each material on the Creator 5, so this
module leaves those alone. It chooses the right preset, then adjusts the
process settings that depend on what the part is for.

Every value set here is listed in docs/SETTINGS.md with its reasoning. They
are starting points drawn from common FDM practice, not measured optima for
your machine; tune them in MATERIALS / STRENGTH / QUALITY below.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import profiles


class RecommendError(ValueError):
    pass


@dataclass(frozen=True)
class Material:
    key: str
    label: str
    presets: tuple[str, ...]          # preset base names, best first
    patterns: tuple[str, ...]         # regexes matched against the requested filament
    process: dict = field(default_factory=dict)
    warps: bool = False
    flexible: bool = False
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


_ENCLOSURE = ("This material shrinks as it cools. Print it only with the chamber closed and warm; "
              "on a machine without chamber heating expect warping on parts wider than about 80 mm.")
_DRY = "Dry the spool before printing; damp filament gives stringing, rough walls and weak layers."
_FIBRE = "Fibre-filled filament is stiffer but more brittle between layers; orient the part so loads run along the layers."

# Order matters: the first material whose pattern matches wins, so specific
# grades sit above their plain parent.
MATERIALS: tuple[Material, ...] = (
    Material("PLA_SILK", "Silk PLA", ("Flashforge PLA Silk", "Generic PLA Silk"), (r"silk",),
             process={"outer_wall_speed": "60", "inner_wall_speed": "120"},
             notes=("Slow, even outer walls keep the silk sheen consistent.",),
             warnings=("Silk PLA bonds weakly between layers; avoid it for load-bearing parts.",)),
    Material("PLA_CF", "PLA-CF", ("Flashforge PLA-CF",), (r"pla[\s_-]*cf", r"carbon.*pla", r"pla.*carbon"),
             warnings=(_FIBRE, _DRY)),
    Material("PLA_MATTE", "Matte PLA", ("Flashforge PLA Matte", "Flashforge PLA Basic"), (r"matte.*pla|pla.*matte",),
             notes=("Matte PLA hides layer lines well; standard quality is usually enough.",)),
    Material("PLA_HS", "High-speed PLA", ("Flashforge HS PLA", "Flashforge PLA Basic"),
             (r"hs[\s_-]*pla", r"high[\s_-]*speed.*pla", r"pla.*high[\s_-]*speed")),
    Material("PLA_PRO", "PLA Pro / PLA+", ("Flashforge PLA Pro", "Flashforge PLA Basic"),
             (r"pla[\s_-]*(pro|\+|plus)", r"tough.*pla")),
    Material("PLA_SPECIAL", "Effect PLA", ("Flashforge PLA Galaxy", "Flashforge PLA Basic"), (r"(galaxy|sparkle|glitter).*pla|pla.*(galaxy|sparkle|glitter)",)),
    Material("PLA_METAL", "Metal-effect PLA", ("Flashforge PLA Metal", "Flashforge PLA Basic"), (r"metal.*pla|pla.*metal",)),
    Material("PLA_LUMINOUS", "Glow PLA", ("Flashforge PLA Luminous", "Flashforge PLA Basic"), (r"(luminous|glow).*pla|pla.*(luminous|glow)",)),
    Material("PETG_CF", "PETG-CF", ("Flashforge PETG-CF",), (r"petg[\s_-]*cf",),
             process={"support_top_z_distance": "0.25"}, warnings=(_FIBRE, _DRY)),
    Material("PET_CF", "PET-CF", ("Flashforge PET-CF",), (r"pet[\s_-]*cf",), warps=True, warnings=(_FIBRE, _DRY)),
    Material("PETG_HS", "High-speed PETG", ("Flashforge HS PETG", "Flashforge PETG Pro"),
             (r"hs[\s_-]*petg", r"high[\s_-]*speed.*petg"), process={"support_top_z_distance": "0.25"}, warnings=(_DRY,)),
    Material("PETG_CLEAR", "Transparent PETG", ("Flashforge PETG Transparent", "Flashforge PETG Pro"),
             (r"(transparent|clear|translucent).*petg", r"petg.*(transparent|clear|translucent)"),
             process={"support_top_z_distance": "0.25"}, warnings=(_DRY,)),
    Material("PETG", "PETG", ("Flashforge PETG Pro", "Flashforge HS PETG"), (r"petg", r"\bpet\b"),
             process={"support_top_z_distance": "0.25"},
             notes=("A wider gap above supports stops PETG welding itself to them.",),
             warnings=(_DRY,)),
    Material("ABS_GF", "ABS-GF", ("Flashforge ABS-GF",), (r"abs[\s_-]*gf",), warps=True, warnings=(_ENCLOSURE, _FIBRE)),
    Material("ABS", "ABS", ("Flashforge ABS Basic",), (r"\babs\b",), warps=True, warnings=(_ENCLOSURE,)),
    Material("ASA_CF", "ASA-CF", ("Flashforge ASA-CF",), (r"asa[\s_-]*cf",), warps=True, warnings=(_ENCLOSURE, _FIBRE)),
    Material("ASA_GF", "ASA-GF", ("Flashforge ASA-GF",), (r"asa[\s_-]*gf",), warps=True, warnings=(_ENCLOSURE, _FIBRE)),
    Material("ASA", "ASA", ("Flashforge ASA Basic",), (r"\basa\b",), warps=True,
             notes=("ASA resists sunlight and weather, so it suits outdoor parts.",), warnings=(_ENCLOSURE,)),
    Material("TPU_64D", "TPU 64D", ("Flashforge TPU-64D", "Generic TPU-64D"), (r"64\s*d",), flexible=True),
    Material("TPU_90A", "TPU 90A", ("Flashforge TPU-90A", "Generic TPU-90A"), (r"90\s*a",), flexible=True),
    Material("TPU", "TPU 95A", ("Flashforge TPU-95A", "Generic TPU-95A"), (r"tpu|tpe|flex",), flexible=True,
             warnings=(_DRY,)),
    Material("PAHT_CF", "PAHT-CF", ("Flashforge PAHT-CF", "FusRock PAHT-CF"), (r"paht",), warps=True, warnings=(_ENCLOSURE, _FIBRE, _DRY)),
    Material("PPA_CF", "PPA-CF", ("Flashforge PPA-CF",), (r"ppa",), warps=True, warnings=(_ENCLOSURE, _FIBRE, _DRY)),
    Material("PPS_CF", "PPS-CF", ("Flashforge PPS-CF",), (r"pps",), warps=True, warnings=(_ENCLOSURE, _FIBRE, _DRY)),
    Material("PA_CF", "PA-CF (nylon)", ("Flashforge PA-CF", "FusRock PAHT-CF"), (r"\bpa\b|pa[\s_-]*cf|nylon",), warps=True,
             warnings=(_ENCLOSURE, _FIBRE, _DRY)),
    Material("HIPS", "HIPS", ("Flashforge HIPS",), (r"hips",), warps=True, warnings=(_ENCLOSURE,)),
    Material("PVA", "PVA (soluble support)", ("Flashforge PVA", "Generic BVOH"), (r"pva|bvoh",),
             warnings=("PVA and BVOH are support materials; they make poor standalone parts.", _DRY)),
    Material("PLA", "PLA", ("Flashforge PLA Basic", "Generic PLA"), (r"pla",)),
)


@dataclass(frozen=True)
class Strength:
    wall_mm: float      # total wall thickness
    infill: int         # percent
    pattern: str
    top_mm: float
    bottom_mm: float
    why: str


STRENGTH: dict[str, Strength] = {
    "decorative": Strength(0.8, 10, "gyroid", 0.8, 0.6,
                           "Display parts: thin shell and light infill save time and filament."),
    "standard": Strength(1.2, 15, "gyroid", 1.0, 0.8,
                         "General use: three-wall shell with gyroid infill, which is equally stiff in every direction."),
    "functional": Strength(1.6, 25, "gyroid", 1.2, 1.0,
                           "Working parts such as brackets and holders: walls carry most of the load, so the shell is thickened first."),
    "structural": Strength(2.4, 40, "cubic", 1.4, 1.2,
                           "Heavily loaded parts: thick walls plus dense cubic infill. Beyond this, change material or redesign."),
}

# Layer height as a fraction of nozzle diameter.
QUALITY: dict[str, tuple[float, str]] = {
    "draft": (0.65, "Thick layers for speed; layer lines are visible."),
    "standard": (0.5, "Balanced layer height for everyday parts."),
    "fine": (0.3, "Thin layers and slower outer walls for a smooth surface; roughly doubles print time."),
}

AUTO, ON, OFF = "auto", "on", "off"


def match_material(filament: str) -> Material:
    text = filament.lower().strip()
    if not text:
        raise RecommendError("Name a filament, for example 'PLA', 'PETG' or 'TPU 95A'.")
    for material in MATERIALS:
        if any(re.search(p, text) for p in material.patterns):
            return material
    known = ", ".join(sorted({m.label for m in MATERIALS}))
    raise RecommendError(f"Filament '{filament}' is not recognised. Known types: {known}.")


def _pick_layer(heights: list[float], nozzle: float, quality: str, layer_height: float | None) -> float:
    target = layer_height if layer_height else QUALITY[quality][0] * nozzle
    return min(heights, key=lambda h: (abs(h - target), h))


def recommend(
    profiles_dir: Path,
    filament: str,
    quality: str = "standard",
    strength: str = "standard",
    nozzle: str = "0.4",
    analysis: dict | None = None,
    supports: str = AUTO,
    brim: str = AUTO,
    layer_height: float | None = None,
    precise_fit: bool = False,
    smooth_top: bool = False,
    pro: bool = False,
) -> dict:
    """Build a full settings plan. Pure function: nothing is written or sliced."""
    if quality not in QUALITY:
        raise RecommendError(f"quality must be one of {', '.join(QUALITY)}")
    if strength not in STRENGTH:
        raise RecommendError(f"strength must be one of {', '.join(STRENGTH)}")
    for label, value in (("supports", supports), ("brim", brim)):
        if value not in (AUTO, ON, OFF):
            raise RecommendError(f"{label} must be auto, on or off")

    material = match_material(filament)
    rationale: list[str] = []
    warnings: list[str] = list(material.warnings)

    # Presets -----------------------------------------------------------------
    machine = profiles.machine_name(nozzle, pro)
    available = profiles.filament_presets(profiles_dir, nozzle, pro)
    suffix = "@FF C5P" if pro else "@FF C5"
    filament_preset = next((f"{base} {suffix}" for base in material.presets if f"{base} {suffix}" in available), None)
    if filament_preset is None:
        raise RecommendError(f"No {material.label} preset exists for the {nozzle} mm nozzle in this slicer install.")
    rationale.append(f"Filament preset '{filament_preset}' supplies temperatures, cooling, flow limit and retraction for {material.label}.")

    layers = profiles.process_presets(profiles_dir, nozzle, pro)
    if not layers:
        raise RecommendError(f"No process presets found for the {nozzle} mm nozzle.")
    layer = _pick_layer(list(layers), float(nozzle), quality, layer_height)
    process_preset = layers[layer]
    base = profiles.flatten(profiles_dir, "process", process_preset)
    rationale.append(f"{layer:g} mm layers ('{process_preset}'). {QUALITY[quality][1]}")
    if layer_height and abs(layer - layer_height) > 0.011:
        warnings.append(f"No preset at {layer_height:g} mm for this nozzle; used the nearest, {layer:g} mm.")

    overrides: dict[str, str] = {}

    # Strength ----------------------------------------------------------------
    tier = STRENGTH[strength]
    line_width = float(base.get("outer_wall_line_width") or base.get("line_width") or float(nozzle) * 1.05)
    walls = max(2, math.ceil(tier.wall_mm / line_width - 0.05))
    top_layers = max(3, math.ceil(tier.top_mm / layer - 1e-6))
    bottom_layers = max(3, math.ceil(tier.bottom_mm / layer - 1e-6))
    overrides.update({
        "wall_loops": str(walls),
        "sparse_infill_density": f"{tier.infill}%",
        "sparse_infill_pattern": tier.pattern,
        "top_shell_layers": str(top_layers),
        "top_shell_thickness": f"{tier.top_mm:g}",
        "bottom_shell_layers": str(bottom_layers),
        "bottom_shell_thickness": f"{tier.bottom_mm:g}",
    })
    rationale.append(f"{strength.capitalize()} strength: {walls} walls, {tier.infill}% {tier.pattern} infill, "
                     f"{top_layers} top and {bottom_layers} bottom layers. {tier.why}")
    if material.flexible:
        rationale.append("For flexible filament, wall count and infill set how firm the part feels; lower both for a softer part.")
    if strength in ("functional", "structural"):
        rationale.append("Printed parts are weakest between layers. Orient the model so the main load runs along the layers, not across them.")

    # Quality -----------------------------------------------------------------
    if quality == "fine":
        current = float(base.get("outer_wall_speed") or 200)
        overrides["outer_wall_speed"] = f"{min(current, 100):g}"
        if walls >= 3:
            overrides["wall_sequence"] = "inner-outer-inner wall"
            rationale.append("Fine quality: outer wall capped at 100 mm/s and printed between inner walls for a cleaner surface.")
        else:
            rationale.append("Fine quality: outer wall capped at 100 mm/s for a cleaner surface.")
    if precise_fit:
        overrides["wall_sequence"] = "inner wall/outer wall"
        overrides["precise_outer_wall"] = "1"
        rationale.append("Precise fit: outer wall spacing tightened so mating dimensions print closer to the model.")
    if smooth_top:
        overrides["ironing_type"] = "top"
        rationale.append("Top surfaces ironed for a smooth finish; adds time on parts with large flat tops.")

    # Material-specific process changes ---------------------------------------
    for key, value in material.process.items():
        if key.endswith("_speed") and key in overrides:
            value = f"{min(float(value), float(overrides[key])):g}"
        overrides[key] = value
    rationale.extend(material.notes)

    # Single-material job: no purge tower needed.
    overrides["enable_prime_tower"] = "0"

    # Shape-aware choices -----------------------------------------------------
    shape = analysis if analysis and analysis.get("analysed") else None
    if shape and not shape["fits_bed"]:
        if shape["fits_bed_if_rotated"]:
            warnings.append(f"Model is {shape['size_mm']} mm and only fits the 256 mm bed if rotated.")
        else:
            warnings.append(f"Model is {shape['size_mm']} mm, larger than the 256 x 256 x 256 mm build volume. Scale or split it.")

    want_support = supports == ON
    if supports == AUTO and shape:
        want_support = shape["overhang_fraction"] > 0.01
        if want_support:
            rationale.append(f"Supports on: {shape['overhang_fraction']:.1%} of the surface overhangs past 45 degrees.")
        else:
            rationale.append("Supports off: no significant overhangs found.")
    elif supports == AUTO:
        rationale.append("Supports left off: the model was not analysed. Pass supports='on' if it has overhangs.")
    if want_support:
        overrides["enable_support"] = "1"
        overrides["support_type"] = "tree(auto)"
        if material.flexible:
            warnings.append("Supports are hard to remove from flexible filament; reorient the part if possible.")
    else:
        overrides["enable_support"] = "0"

    want_brim = brim == ON
    if brim == AUTO:
        reasons = []
        if material.warps:
            reasons.append(f"{material.label} tends to lift at the corners")
        if shape and shape["height_to_footprint"] > 2.5:
            reasons.append("the part is tall for its base")
        if shape and 0 < shape["bed_contact_area_mm2"] < 150:
            reasons.append("very little of the part touches the bed")
        want_brim = bool(reasons)
        if reasons:
            rationale.append("Brim added because " + " and ".join(reasons) + ".")
    if want_brim:
        overrides["brim_type"] = "outer_only"
        overrides["brim_width"] = "8" if material.warps else "5"
    else:
        overrides["brim_type"] = "no_brim"

    return {
        "material": material.label,
        "machine_preset": machine,
        "process_preset": process_preset,
        "filament_preset": filament_preset,
        "layer_height_mm": layer,
        "process_overrides": overrides,
        "filament_overrides": {},
        "rationale": rationale,
        "warnings": warnings,
    }
