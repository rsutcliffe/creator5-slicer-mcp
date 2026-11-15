# What the server sets, and why

The server works in two layers.

1. **Presets from the slicer.** OrcaSlicer ships Creator 5 presets for the machine, for each layer height, and for each filament. The filament preset carries nozzle and bed temperature, cooling, maximum flow and retraction. The server selects the right one and leaves those values alone.
2. **Adjustments for the job.** The server then overrides a small set of process settings according to the quality and strength you ask for, the filament family, and the shape of the model.

The values below are starting points drawn from common FDM practice. They have not been tuned by measurement on a Creator 5. Change them in `src/creator5_mcp/recommend.py`; each table there is plain data.

## Quality: layer height

Layer height is chosen as a fraction of nozzle diameter, then snapped to the nearest Creator 5 preset.

| Level | Fraction of nozzle | 0.4 mm nozzle | Extra changes |
|---|---|---|---|
| draft | 0.65 | 0.24 mm | none |
| standard | 0.5 | 0.20 mm | none |
| fine | 0.3 | 0.12 mm | outer wall capped at 100 mm/s; outer wall printed between inner walls when there are three or more |

Thinner layers reduce visible stepping on curves and slopes. They do not make a part meaningfully stronger, and they lengthen the print.

## Strength: walls, infill and shells

Wall thickness is set in millimetres and converted to a wall count using the preset's line width, so the result holds across nozzle sizes.

| Level | Wall thickness | Walls at 0.4 mm | Infill | Top | Bottom | Intended for |
|---|---|---|---|---|---|---|
| decorative | 0.8 mm | 2 | 10% gyroid | 0.8 mm | 0.6 mm | display models |
| standard | 1.2 mm | 3 | 15% gyroid | 1.0 mm | 0.8 mm | everyday parts |
| functional | 1.6 mm | 4 | 25% gyroid | 1.2 mm | 1.0 mm | brackets, holders, clips |
| structural | 2.4 mm | 6 | 40% cubic | 1.4 mm | 1.2 mm | heavily loaded parts |

Reasoning: in a printed part most bending load is carried by the outer shell, so extra walls add strength more efficiently than extra infill. Gyroid is used because it is similarly stiff in all directions. Top and bottom thickness is converted to a layer count from the chosen layer height, with a minimum of three layers.

Orientation matters more than any of these numbers. A part is weakest between layers, so place it on the bed with the main load running along the layers. The server leaves the model's orientation alone unless you pass `auto_orient=true`.

## Shape-aware choices

For STL files the server measures the model before slicing.

| Finding | Action |
|---|---|
| More than 1% of the surface faces downward past 45 degrees, clear of the bed | tree supports on |
| Height more than 2.5 times the shorter base dimension | 5 mm outer brim |
| Less than 150 mm² touching the bed | 5 mm outer brim |
| Material prone to warping | 8 mm outer brim |
| Larger than 256 x 256 x 256 mm | warning returned |

`supports` and `brim` accept `auto`, `on` or `off`, so you keep the final say.

## Filament families

| You ask for | Preset used | Process changes | Warnings returned |
|---|---|---|---|
| PLA | Flashforge PLA Basic | none | none |
| PLA+, PLA Pro | Flashforge PLA Pro | none | none |
| Matte, high-speed, galaxy, metal, glow PLA | matching Flashforge preset | none | none |
| Silk PLA | Flashforge PLA Silk | outer wall 60 mm/s, inner wall 120 mm/s | weak layer bonding |
| PLA-CF | Flashforge PLA-CF | none | brittle between layers; dry first |
| PETG, HS PETG, clear PETG | Flashforge PETG Pro / HS PETG / PETG Transparent | support top gap 0.25 mm | dry first |
| PETG-CF, PET-CF | matching preset | support top gap 0.25 mm (PETG-CF) | brittle between layers; dry first |
| ABS, ABS-GF, ASA, ASA-CF, ASA-GF, HIPS | matching preset | 8 mm brim | needs a closed, warm chamber |
| TPU 95A, 90A, 64D | matching preset | none | supports are hard to remove; dry first |
| PA-CF, PAHT-CF, PPA-CF, PPS-CF | matching preset | 8 mm brim | needs a closed, warm chamber; dry first |
| PVA, BVOH | matching preset | none | support material only |

The purge tower is switched off for every job, because the server currently slices single-material prints.

## Optional flags

| Flag | Effect |
|---|---|
| `precise_fit` | inner wall first, precise outer wall on, for parts that mate with other parts |
| `smooth_top` | irons top surfaces |
| `layer_height` | picks the preset nearest to an exact layer height |
| `process_overrides` | any OrcaSlicer process key, applied last |
| `filament_overrides` | any OrcaSlicer filament key, applied last |

Override keys that the base preset does not define are still passed to the slicer, and are listed in the warnings in case of a typing mistake.
