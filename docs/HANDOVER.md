# Handover: Creator 5 slicing by MCP

Written 15 November 2026 at the end of the first session, which ran in a cloud chat with no ability to run software on the owner's Mac. Updated the same day by the second session, run in Claude Code on the Mac, in two rounds.

## 1. Where things stand

The chain works end to end and the first print has been sent:

```
model (STL, OBJ or 3MF) + filament + purpose
  -> optional: Flash Studio's CLI orients it and exports one STL
  -> settings chosen (Creator 5 presets + designer's saved supports/brim + our overrides)
  -> project .3mf written by this server
  -> that same project sliced by Flash Studio's CLI (time, weight, and every setting read back)
  -> opened in a new Flash Studio window, Slice, Print over Flash Studio's own printer link
```

Proven on the owner's Mac:

| Step | Evidence |
|---|---|
| Preset lookup and flattening against Flash Studio's presets | All 156 Creator 5 filament presets resolve to 1.75 mm |
| Slicing through Flash Studio's CLI | Clean exit, complete G-code, sensible time and weight |
| Settings reach the G-code | Flow, speed, temperatures, walls and infill read back from the file |
| Project file opens in Flash Studio with settings applied | Presets shown as modified; sliced; "Send completed" to Creator 5 (session 1, STL model) |
| Downloaded 3MF becomes a project | PS5 holder 3MF: mesh read with 10,810 triangles and 50.2 x 87.0 x 60.0 mm, identical to Flash Studio's own export (session 2) |
| Designer's supports kept | PS5 holder: `enable_support = 1` in the G-code, 343 support sections; session 1's code gave 0 |
| The project itself slices, and every chosen setting reaches the G-code | Flash Studio CLI slices our .3mf with no other settings files; 18 of 18 settings matched (`settings_mismatched` empty) |
| Real-slicer tests | `tests/test_real_slicer.py` (three tests) passes on this Mac, skips elsewhere |
| Multi-colour project | ElijahGames Skeleton-Angel four-colour plate: Flash Studio's CLI sliced our project, named both objects (`Skeleton`, `Angel`), and used all four toolheads (46.4, 34.7, 29.8 and 13.4 g; 1,485 filament changes) |
| Part named by its designer, not the file hash | PS5 holder: G-code object `drzak_na_ps5_ovladac_v3`, was `93b857fe…` |
| `best` quality | PS5 holder on `0.10mm Standard @FF C5`: 2h 51m, 31 g, 18 of 18 settings matched, 415 support sections |
| Spool register | `black pla` saved in `~/Creator5Prints/spools.json`; the request `black-pla` resolved to Creality Ender PLA at 215/60 C |
| Project opens on screen, ready to print | Owner's screenshot, 15 Nov: `open_in_slicer` opened the PS5 holder in a new window with no import dialog; Prepare tab; Flashforge Creator 5; `* Generic PLA`; `0.10mm Standard @FF C5` with its changed tabs marked; model on the plate; Slice plate enabled. A first attempt showed a warning that the precise wall option is ignored; after the fix a second screenshot shows no warning |
| A real print, good quality | Two downloaded PS5 holders, black Ender PLA, 0.20 mm, functional strength; logged in `~/Creator5Prints/print_results.json` and `docs/SETTINGS.md` |
| The MCP protocol path, new code | A separate server process called over stdio by an MCP client: `check_setup`, `slice_model` with `other_filaments`, then `open_in_slicer`, against the real Flash Studio. The two-colour test piece sliced (11 min, 1.95 g and 1.72 g, 22 toolhead changes, 16 of 16 settings) and opened |
| A two-colour project on screen | Owner's screenshot, 15 Nov: filaments 1 `* Generic PLA` and 2 `Flashforge PLA…`, each half in its colour on its toolhead, purge tower placed, Slice plate enabled |
| Each toolhead keeps its own filament settings | Four-colour plate G-code: `nozzle_temperature = 215,210,210,230`, `filament_max_volumetric_speed = 6.7,18,18,12` |
| STEP files | A generated STEP box converted by `cascadio`: exact size (40 x 25 x 12 mm, 12.0 cm³), then oriented and sliced by Flash Studio in `tests/test_real_slicer.py` |
| Pause to embed a part | NFC test coupon in white Elegoo Rapid PETG: 20 layers, 4.0 mm, one `M25` at the start of the layer at 3.0 mm, no supports, 255 C and a 70 C bed, 17 of 17 settings matched. `tests/test_real_slicer.py` also pauses a two-colour part and checks toolhead 2 is used in the first three layers only |
| First two-colour print with a pause | ThinkTribal NFC key tag, white and black Elegoo Rapid PETG, 16 Nov: the pause came after layer 14, the sticker went in and still reads through the roof. Quality only acceptable (see `docs/SETTINGS.md`) |
| Hand changes read back from a saved project | The owner's saved project against the one the server wrote: `enable_support` 0 to 1, model swapped, plus two purge tower values Flash Studio rewrote |

Not yet proven:

- The finished print. On 15 November the owner printed two of the downloaded PS5 holders. He imported the holder into the old project `DualSenseWallHolder_v1.3.3mf` (which kept its file name), added supports by hand in Flash Studio and cloned the part (60.3 g in the printer app). The printer app showed toolhead 1 at 215 C and the bed at 60 C, so the spool's label figures reached the machine. The result has not been assessed yet. The v1.3 hook designed in session 1 was never printed: two of them slice to 100.7 g, and Flash Studio generates no supports for it even with supports on. The server now turns supports on for the downloaded holder by itself (its designer saved them on, and it has 112 mm² of overhang), so the manual step is no longer needed.
- A spool's own temperatures on toolheads 2 to 4. No way has been found to make Flash Studio keep them (section 3, item 28).
- Whether NFC still reads with the QR code above the sticker: the owner has confirmed the code scans, not yet the tap.
- STEP files with curved surfaces or from a real CAD program. Only a flat-faced box written by the test suite has been converted.
- The MCP tools called by Claude itself. The server was driven over the MCP protocol by a test client, but Claude had not been restarted, so the model's own choice of tool arguments from one sentence is untested.
- The corrected two-colour project on screen. The screenshot was of the project written before the `filament_self_index` fix.
- The MCP tools called from Claude Desktop or Claude Code. The running MCP server loads the main checkout's code, so restart Claude after merging to pick up session 2.
- Colour-painted models. The writer carries `paint_color` through and a unit test checks the file, but no real painted model has been sliced.
- A multi-colour print on the machine. The four-colour plate was sliced, not printed.

## 2. The machine

- MacBook Pro, Apple Silicon, Python 3.14. Repo at `~/Code/creator5-slicer-mcp`, virtualenv in `.venv`, output in `~/Creator5Prints`.
- **Flash Studio** at `/Applications/Flash Studio.app`, bundle id `com.flashforge.orca-flashforge`. It is Orca-Flashforge 1.7, built on OrcaSlicer 2.3.0, and reports `SLIC3R_VERSION 01.10.01.50`. Intel build, runs under Rosetta. Its presets are plain JSON in `Contents/Resources/profiles`.
- **OrcaSlicer 2.5.0-rc** is also installed. Its presets are packed into `<vendor>.opc` files (magic `ZCRO`), which this server cannot read, so `config._pick` chooses Flash Studio.
- Printer: Flashforge Creator 5, 0.4 mm hardened nozzle, textured PEI plate, 256 mm cube build volume, already bound in Flash Studio.
- Filament in use: **Creality Ender PLA, black**. Label: 1.75 mm, nozzle 190 to 220 C, bed 25 to 60 C, speed 40 to 80 mm/s. The server knows it as `ender-pla`: nozzle 215, bed 60, flow capped at 6.7 mm3/s.

## 3. Things that were learned the hard way

Each of these cost a round trip through the owner. Do not relearn them.

1. **Same-named presets in two vendor folders.** Flash Studio has an old `fdm_filament_common` under `Flashforge/` (2.85 mm filament) and the current one under `OrcaFilamentLibrary/` (1.75 mm). A parent must be looked up in the same vendor folder as the preset naming it. Getting this wrong produced G-code with about 38% of the needed plastic. `project.project_settings` now refuses any filament that does not resolve to 1.75 mm.
2. **`--export-3mf` crashes Flash Studio's CLI on macOS.** It renders thumbnails through OpenGL with no window: "Invalid OpenGL version", exit code -11. The G-code is written before the crash. Do not use that flag.
3. **Flash Studio will not print a G-code file.** Opening one gives a view-only preview with Slice and Print disabled. Open a project instead.
4. **The project format** (`project.py`). Flash Studio accepts a .3mf with plain inline geometry plus `Metadata/project_settings.config`, provided:
   - `3D/3dmodel.model` carries `<metadata name="Application">BambuStudio-01.10.01.50</metadata>`;
   - the settings name the system presets in `printer_settings_id`, `print_settings_id`, `filament_settings_id`;
   - `filament_colour` has one entry per filament (the loader counts filaments from it);
   - `inherits_group` is `["", "", ""]` and `different_settings_to_system` lists, per preset, the keys that differ, joined by `;`, in the order process, filament, printer.
   On load, any key not in that list is reset to the system preset's value, and the preset is shown as modified. Source: `PresetBundle::load_config_file_config` and `PresetCollection::load_external_preset` in `FlashForge/Orca-Flashforge`.
7. **Downloaded models are 3MF, and their designers set them up.** Session 1 wrote projects from STL only, so the first real model the owner chose produced no project. That file also carried the designer's tree supports at 30 degrees, which the server ignored. `model.load_mesh` now reads 3MF, and `model.designer_settings` keeps the shape-dependent choices.
8. **Slicing the original model with `--load-settings` mixes in the file's own settings.** For a 3MF that carries settings, the G-code kept the designer's four filaments beside ours. Slice our project instead. It carries everything, and the CLI ranks project settings lowest only when other settings are also passed.
9. **`--orient 1 --export-stl` works headless**, unlike `--export-3mf`. Use it to orient once and build both the project and the G-code from the result.
10. **The import dialog only appears when the plate is not empty.** `project_load_behaviour` defaults to `ask_when_relevant`, and `Plater::open_3mf_file` asks only when `model().objects` is non-empty. With `single_instance: false`, `open -n` gives a fresh, empty window, so there is no dialog and an open project is never replaced.
11. **Overhang as a share of the surface misses real overhangs.** 112 mm² on the PS5 holder was 0.6%, below the old 1% rule. Use area.
12. **Neither slicer's command line reads STEP.** Flash Studio and OrcaSlicer 2.5 both answer "Unknown file format. Input file must have .stl, .obj, .amf(.xml) extension"; only their windows import STEP. The optional `cascadio` library (OpenCASCADE, about 70 MB) converts STEP to OBJ, and the server then works from the mesh. It is run in a separate process, because it prints to standard output and would corrupt the MCP connection inside the server.
13. **Names and toolheads live in `Metadata/model_settings.config`.** `_BBS_3MF_Importer` matches each `<part id>` to the object id of a component, and reads `name` and `extruder` metadata from the object and each part. Write every object as a parent with components, even a single part. The G-code's `EXCLUDE_OBJECT_DEFINE NAME=` lines show the names Flash Studio loaded, so this can be checked without the window.
14. **Filament settings are one value per toolhead.** With N filaments, `filament_settings_id`, `filament_colour` and every filament key have N entries, and `different_settings_to_system` and `inherits_group` have N + 2 (process, each filament, printer).
15. **`filament used [g]` in the G-code is per toolhead.** Sum it for the total.
16. **Importing a model into an open project drops its settings.** The owner imported the downloaded holder into an older project, so the designer's supports were lost and he added them by hand. Opening our project in a new window avoids this.
17. **Flash Studio rewrites a project when it saves.** Every per-toolhead list is padded to four entries, numbers are reformatted (`0.50` becomes `0.5`), and `prime_tower_infill_gap` and `wipe_tower_wall_type` come back as `100%` and `rectangle` where the presets say `165%` and `rib`. Compare distinct values, not raw lists (`results._plain`). The two purge tower values mean our multi-colour estimate may differ slightly from Flash Studio's own slice.
18. **Inner-outer-inner wall order makes Flash Studio warn** that the precise wall option is ignored, when the preset leaves that option unset (its built-in default is on). Set `precise_outer_wall` to 0 with that order.
19. **The computer-use tools see only the first Flash Studio process.** A window opened with `open -n` belongs to a second process and cannot be captured that way. Its title is readable through System Events; its Prepare tab is custom-drawn and not exposed to accessibility. Ask the owner for a screenshot.
20. **A test that segfaults on purpose raises a macOS crash report every run.** The fake slicer now dies by SIGKILL.
21. **A multi-filament project needs `filament_self_index`.** Without `["1", "2", …]` the slicer reads toolhead 1's values for every toolhead: toolhead 2 printed at 215 C where its preset says 210. Flash Studio writes this key when it saves. Found only because the G-code check compares every toolhead's value.
22. **A gap in a per-toolhead list makes the slicer drop every setting, silently.** PLA with PETG: one preset defines `textured_cool_plate_temp`, the other does not; the list held a null; the slicer logged `load_from_json … invalid json array`, exited 0 and sliced with its defaults. Fill gaps from a preset that has the key, and treat that log line as a failed slice.
23. **The purge tower values are not ours to change.** The Creator 5 preset itself says `165%` and `rib`; the saved single-filament project had `100%` and `rectangle` with the tower off. Flash Studio's public source does not contain these keys (the app is newer than the repo), so the reason is unknown. Left as the preset has them.
24. **A flow cap from the label's speed halves at half the layer height.** Use a proven `max_flow` per spool.
28. **Only toolhead 1's filament keeps a spool's own figures.** Flash Studio's slice of every two-colour PETG project put toolhead 2 on `Generic HS PETG @FF C5` (235 C, bed 80 C), whatever the project said. Four ways were tried on the machine and all were replaced: changed values inline (as toolhead 1 has them), a preset embedded in the project (`Metadata/filament_settings_N.config`, which also raises a "Customized Preset" notice), a preset saved in Flash Studio's own window, and a different stock preset (`Flashforge PETG Basic`). Why is not known: the public source is older than the app, and `PresetBundle::load_config_file_config` there explains only the inline case. The command line does none of this and takes the project's values as written, so **its G-code is not proof of what Flash Studio prints for toolheads 2 to 4**. The server now gives later toolheads the stock preset of the same material with the nearest bed temperature, then the nearest nozzle temperature, and says so in a warning. For Elegoo Rapid PETG that is `Generic HS PETG`, the preset Flash Studio picks itself, so the project and the print agree.
29. **Flash Studio's own G-code can be read without printing.** After the owner presses Slice plate, the session folder under `$TMPDIR/orca-flashforge/model/<day>/<time>#<pid>#…/Metadata/.<pid>.0.gcode` holds what it sliced, with the same settings block. This is the way to check what the window does; the owner's one click replaces a test print. Since Flash Studio 1.7.22 the folder is `~/Library/Application Support/Orca-Flashforge/recovery/<time>#<pid>#…/Metadata/.<pid>.0.gcode`; the `$TMPDIR` one stays empty. Read on 17 Nov 2026 for the key tag with QR: 1.7.22 slices toolhead 1 at 255 C, toolhead 2 as `Generic HS PETG @FF C5` at 235 C, bed 80 C, one `M25` at Z 3.0, the same as the command-line slice.
30. **`bed_temperature_formula = by_first_filament` follows whichever toolhead prints first on each layer**, not toolhead 1. It gave 70 C on layer 1 and 85 C from layer 2. Not used.
31. **What the Creator 5 does at a pause (`M25`)**: it stops after the layer change, the bed holds its temperature, the nozzles switch off and cool, and they reheat when the owner presses continue. Seen on the Device tab, 17 Nov.
33. **Flash Studio was updated to 1.7.22 on 17 Nov 2026**, part-way through the key tag work. Its presets now have `exclude_object = 0`, so object names no longer appear in the G-code unless that setting is switched on (the tests do). Everything in items 28 to 31 was seen on the version before it and has not been re-checked on 1.7.22.
32. **Small lettering on the first layer.** Strokes of 0.6 to 0.8 mm (one and a half to two nozzle passes) blurred and lost the dots on the i's. Enlarging the logo from 26 to 30 mm, `wall_generator = arachne` and a 20 mm/s first layer fixed it. A QR code with 1.12 mm squares on the top face scans.
26. **A pause is a `<layer top_z type="1">` entry in `Metadata/custom_gcode_per_layer.xml`** (`_BBS_3MF_Exporter::_add_custom_gcode_per_print_z_file_to_archive`). `top_z` is the top of the layer the pause comes before. The command line honours it: `;PAUSE_PRINT` and the preset's `M25` appear once, straight after the layer change. What the Creator 5 does on `M25` (where the head parks, whether the bed stays hot) is not documented anywhere found; the first test print will show it.
27. **Flash Studio's PETG presets do not suit every PETG.** Elegoo Rapid PETG's label says 240 to 270 C and a 65 to 75 C bed; the HS PETG preset prints at 235 C and every PETG preset heats the bed to 80 or 85 C. Label figures go in the spool register.
25a. **With mixed filaments the slicer heats the bed to the highest temperature asked for**, whichever toolhead it is on (PLA with PETG: `M140 S85` both ways round). The plan warns when toolheads differ by 10 C or more.
25b. **The server holds its code in memory until Claude restarts.** A pull or a merge changes nothing until then. `check_setup` reports `code_revision` and `restart_needed`; check it before testing through Claude.
25. **With an OBJ input the slicer's command line does not create its `--outputdir`** and aborts on `create_directory`, which raises a macOS "Flash Studio quit unexpectedly" dialog. Create the folder first. Any abort of the headless slicer shows that dialog to the owner, so test new command lines sparingly.
5. **Speed is best limited through `filament_max_volumetric_speed`**, not individual speed settings. It covers every move.
6. **Curly quotes.** Two commands failed because the chat app changed `"` to typographic quotes. The terminal command takes hyphenated words (`--filament ender-pla`) for this reason.

34. **Why a changed filament on toolheads 2 to 4 is swapped: part found, not fixed (17 Nov 2026).** Flash Studio's log (`~/Library/Application Support/Orca-Flashforge/log/debug_*.log.0`) said of the embedded preset: `validate_preset: preset_name Flashforge HS PETG @FF C5 (toolhead 2), no inherit, set to not found`. `PresetBundle::validate_presets` needs `inherits_group[toolhead + 1]` to name the stock preset; the project left it empty. Two patched copies of the six-tag project were opened in 1.7.22: one with `inherits_group` filled, one with the changed keys also listed in `different_settings_to_system`. Both passed validation and both still showed `Generic HS PETG` on toolhead 2. So a later step swaps it, and that step is not in the public source: `FlashForge/Orca-Flashforge` stops at 1.7.15 and has no Creator 5 presets. The nearest-stock-preset rule in item 28 stays. Read the log before any further attempt; it names each decision.
35. **Fine detail is measured and warned about.** `model.thin_walls` casts rays through each part's upright walls. On the key tag it finds 0.63 mm in the logo and 0.47 mm in the body. See `docs/SETTINGS.md`.
36. **`check_flash_studio_slice` reads Flash Studio's own G-code** by matching `origin.txt` in the window's session folder to the project path. The folder goes when the window closes.

## 4. Lessons on approach

From this session and from the article the owner shared (How-To Geek, "This is what AI coding actually excels at", 2026), in which Codex got a Creator 5 Pro working with Orca Slicer and Home Assistant in a few tries:

- **Start from known working source and adapt it.** The article's author credits this for the result. The first session wrote a server from scratch against OrcaSlicer's source when the owner runs Flash Studio. Reading Flash Studio's own code is what finally produced a working project file, in minutes.
- **Run where the software runs.** That agent ran on the author's Mac and could test its own work. The first session could not, and should have said so at the start and handed over to Claude Code.
- **Agree the end state before building.** The owner wanted "open in the slicer, ready to print". The session built "produce G-code" and only found the gap at the last step.
- **Check facts that are checkable.** The controller's dimensions were on a page already fetched; the hook was designed from a guess and redrawn twice. The spool's limits were on its label.
- **Do not build new software to do a job once.** If the owner asks to print a part, get the part printing, then offer the tooling.
- **Say what you cannot do, early and once.** Then pick the route that works, even if it is not the one first proposed.

## 5. What to do next, in order

Done in session 2: the MCP path writes and opens the project without prompting (server instructions and `open -n`); the integration tests (`tests/test_real_slicer.py`); the filament colour from the request; the spool register; object names; multi-colour and multi-object 3MFs; a `best` quality level; the LAN upload and USB copy removed; supports checked in the G-code; a print result log with hand changes read from a saved project; CI; the smoke test writing to a throwaway folder; a proven flow limit per spool; a two-colour test piece; old test jobs moved to the Trash (`~/.Trash/Creator5Prints_tidy_20261115`).

1. **Get the rest of the PS5 holder result**: strength and fit. Supports came off cleanly with Flash Studio's default gap and interface, so those settings stay as they are.
2. **Print the two-colour test piece**: `~/Creator5Prints/20261115_195640_Two_colour_test/Two_colour_test.3mf` (black Ender PLA and the blue PLA in toolhead 2). Then the four-colour plate, `~/Creator5Prints/20261115_195659_Skeleton/Skeleton.3mf`.
3. **Run a flow test on the black PLA** (Flash Studio's Calibration menu) and save the result as the spool's `max_flow`. It is 6.7 mm³/s now, from one good print. The blue, teal and magenta PLA in toolheads 2 to 4 have no label figures saved yet.
3a. **After restarting Claude, try one sentence** and check the tool arguments it chooses.
5. **OrcaSlicer 2.5 `.opc` presets.** Only worth reading if the owner moves off Flash Studio.

## 6. The hook, for reference

Abandoned on 15 November 2026: the owner chose a downloaded holder that hangs off the PS5 instead. The generator is kept for reference.

`models/dualsense_hook/build_hook.py` generates the v1.3 DualSense wall hook. It is lofted from cross-sections and prints with the back plate on the bed, no supports. Dimensions were scaled from the drawing at dimensions.com/element/playstation-5-dualsense-controller (160 mm overall width):

- gap between the grips about 80 mm at the body, so the arm is 68 mm wide;
- thumbsticks 16 to 36 mm either side of centre, from 7 mm above the bottom edge, so the lip is 4 mm under them and rises to 14 mm only in the centre;
- cradle slopes 10 degrees back to the wall; arm stress about 0.13 MPa.

It has not yet been checked against a real controller.

## 7. Useful commands

```bash
# update, install, test with the real slicer, register with Claude Desktop and Claude Code
./scripts/setup.sh

# slice and open in Flash Studio as a project
.venv/bin/python -m creator5_mcp.cli MODEL.stl --filament ender-pla --strength functional --copies 2 --open

# tests, including every filament against Flash Studio's real presets
ORCA_TEST_PROFILES_DIR="/Applications/Flash Studio.app/Contents/Resources/profiles" .venv/bin/python -m pytest -q
```
