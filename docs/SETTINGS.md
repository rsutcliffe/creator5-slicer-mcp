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
| fine | 0.3 | 0.12 mm | outer wall capped at 100 mm/s; outer wall printed between inner walls when there are three or more, with the precise wall option off (that sequence makes Flash Studio ignore the option and warn about it) |
| best | 0.25 | 0.10 mm | as fine |

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

Orientation matters more than any of these numbers. A part is weakest between layers, so place it on the bed with the main load running along the layers. The server leaves the model's orientation alone unless you pass `auto_orient=true`. Then Flash Studio's own orienter turns the part, and the project and the G-code are both built from that one turned copy.

## Shape-aware choices

The server measures STL, OBJ and 3MF models before slicing. For a 3MF it reads the parts on the first plate as the designer placed them, including Bambu Studio and OrcaSlicer files that keep each part in a separate file, and leaves out modifier and negative parts.

### The designer's settings come first

A 3MF saved by Bambu Studio, OrcaSlicer or Flash Studio usually carries the settings its designer printed it with. With `supports` and `brim` on `auto`, the server keeps the designer's choices for the settings that depend on the part's shape:

`enable_support`, `support_type`, `support_style`, `support_threshold_angle`, `support_on_build_plate_only`, `brim_type`, `brim_width`, `brim_object_gap`, `raft_layers`.

Only values that differ from the Creator 5 preset are applied, so Flash Studio marks just those as changed. Everything else (printer, filament, temperatures, walls, infill, layer height) still comes from the Creator 5 presets, the spool and the strength and quality you ask for. The designer's file was often made for another printer.

Reason: the first PS5 controller holder came as a 3MF whose designer had turned tree supports on at 30 degrees. The server ignored that and sliced it with no supports.

### Measured from the shape, when the file says nothing

| Finding | Action |
|---|---|
| More than 25 mm² of underside flatter than the support angle (30 degrees, the Creator 5 preset's `support_threshold_angle`), clear of the bed | tree supports on |
| Height more than 2.5 times the shorter base dimension | 5 mm outer brim |
| Less than 150 mm² touching the bed | 5 mm outer brim |
| Material prone to warping | 8 mm outer brim |
| None of the brim reasons above | brim left to the preset's `auto_brim`, which adds one only where the part needs it |
| Larger than 256 x 256 x 256 mm | warning returned |

Supports go on by area, not by share of the surface. The PS5 holder has 112 mm² of overhang, only 0.6% of its surface, which the old 1% rule missed. Tree supports are placed only under the faces that need them, so switching them on costs little when in doubt.

`supports` and `brim` accept `auto`, `on` or `off`. An explicit `on` or `off` beats both the designer's file and the shape check.

## Evidence from prints

Results are logged in `print_results.json` in the output folder. Each row here is a real print.

| Date | Part | Filament | Settings | Changed by hand | Outcome |
|---|---|---|---|---|---|
| 15 Nov 2026 | Downloaded PS5 controller holder, two copies, 60.3 g | Black Creality Ender PLA: Generic PLA @FF C5, nozzle 215 C, bed 60 C, flow cap 6.7 mm³/s | 0.20mm Standard @FF C5, functional strength (4 walls, 25% gyroid, 6 top and 5 bottom layers), ironed top, no brim | Supports switched on; model swapped into an older project; part cloned | Good quality. Supports came off cleanly. Strength and fit not yet reported. |
| 16 Nov 2026 | ThinkTribal NFC key tag, 36 x 62.5 x 4 mm, logo inlay face down, NFC sticker embedded at a pause | White and black Elegoo Rapid PETG on Flashforge HS PETG @FF C5. Asked for 255 C and a 70 C bed; Flash Studio swapped toolhead 2 to Generic HS PETG, so the bed ran at 80 C and the logo likely at 235 C | 0.20mm Standard @FF C5, first layer 0.20 mm, 4 walls, 30% gyroid, 5 shells, no supports, pause before 3.0 mm | None | Acceptable. Works: the tag reads. Logo square and T clean; the small wordmark lost its i dots and fine strokes; a first-layer lip on the bottom edge; wisps of stringing in the loop hole. |

What this supports: the Ender PLA label figures, the functional strength tier and the 0.20 mm preset give a good result on this machine, and Flash Studio's default tree support gap and interface release cleanly from this PLA, so the server leaves them alone. What it does not cover: fine or best quality, other filaments, or more than one toolhead. At fine and best the flow per second is now the same as in this print, but the line speed is higher, so the first fine or best print should be watched.

## How the result is checked

The server writes the project file first, then slices that same file with Flash Studio's command line, taking every setting from the project. So the G-code, the time and the weight describe exactly what you will print from Flash Studio. After slicing, it reads the settings block at the top of the G-code and compares every value it chose. Any difference is returned in `settings_mismatched` and as a warning. The PS5 holder checked 19 of 19.

Supports are checked in the G-code too. `support_sections_in_gcode` counts the stretches of support the slicer printed. If supports are on and the slicer generated none, a note says so. If supports are off and more than 25 mm² of underside is flatter than the support angle, a warning says the part will print over thin air.

## Filament families

| You ask for | Preset used | Process changes | Warnings returned |
|---|---|---|---|
| PLA | Flashforge PLA Basic | none | none |
| Creality Ender PLA | Generic PLA | nozzle 215 C, bed 60 C, flow capped to 80 mm/s, from the spool label | none |
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

The purge tower is switched off for single-filament jobs and on when a job uses more than one toolhead.

## More than one filament

Each toolhead gets its own filament preset, label figures and colour. The process (layer height, walls, infill, speeds) is shared, chosen for toolhead 1's filament. A second material's own process needs are added without replacing anything already chosen, and where both set a speed the slower one is used. Example: silk PLA in toolhead 4 slows the outer wall to 60 mm/s for the whole job.

Colours come, in order, from a colour word in the request, the saved spool, then the model file's own colour for that toolhead.

The filament colour shown in Flash Studio follows a colour word in the request (`black PLA`, `silk gold PLA`). With none, it is black.

## Saved spools

`save_spool` keeps a spool's material, label figures and colour under a name, in `spools.json` in the output folder. A request that contains every word of a spool's name uses it; with two matches the longer name wins. Figures passed with a request take priority over the saved ones.

## Figures from the spool label

Flashforge's filament presets are tuned for Flashforge's own spools. Other makers' filament often wants a cooler bed or a lower speed. Pass the label's figures and they take priority over the preset.

| Input | Effect |
|---|---|
| `nozzle_temp` | sets nozzle temperature for all layers |
| `bed_temp` | sets bed temperature for the smooth and textured plates |
| `max_speed` | caps flow at `max_speed x line width x layer height`, so the slicer slows every move to suit |

Speed is limited through the flow cap, not by editing each speed setting, because the cap also covers infill, top surfaces and any other move.

### A proven flow limit beats the label's speed

A cap worked out from the label's speed shrinks with the layer: 80 mm/s gives 6.7 mm³/s at 0.20 mm but only 3.4 at 0.10 mm. A filament's real limit is how fast it melts, which does not depend on layer height. So a spool can carry `max_flow`, a limit proven by a print or a flow test, and that is used at every layer height.

The black Ender PLA spool has `max_flow` 6.7 mm³/s, proven by the good print of 15 November 2026. On the PS5 holder this cut best quality from 2h 51m to 2h 26m and fine from 2h 11m to 1h 42m. A flow test in Flash Studio (Calibration menu) could raise it further.

## Optional flags

| Flag | Effect |
|---|---|
| `precise_fit` | inner wall first, precise outer wall on, for parts that mate with other parts |
| `smooth_top` | irons top surfaces |
| `layer_height` | picks the preset nearest to an exact layer height |
| `process_overrides` | any OrcaSlicer process key, applied last |
| `filament_overrides` | any OrcaSlicer filament key, applied last |

Override keys that the base preset does not define are still passed to the slicer, and are listed in the warnings in case of a typing mistake.
