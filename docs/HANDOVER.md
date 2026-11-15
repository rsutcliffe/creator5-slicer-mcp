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

Not yet proven:

- The finished print. The hook was abandoned for a downloaded PS5 holder, which the owner set up and printed by hand in Flash Studio, adding supports.
- The session 2 project, opened on screen. The owner declined screen access while a print was running, so the PS5 holder project has not been opened in Flash Studio. The file format is unchanged from session 1's working one apart from the mesh source and colour, and the CLI slices it, but nobody has looked at it in the window.
- `open_in_slicer` now uses `open -n`. This follows from Flash Studio's source (`Plater::open_3mf_file`) and its config (`single_instance: false`, `project_load_behaviour: ask_when_relevant`), but has not been watched on screen.
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
12. **Neither slicer's command line reads STEP.** Flash Studio and OrcaSlicer 2.5 both answer "Unknown file format. Input file must have .stl, .obj, .amf(.xml) extension"; only their windows import STEP. Supporting it needs a converter library, which is the owner's call.
13. **Names and toolheads live in `Metadata/model_settings.config`.** `_BBS_3MF_Importer` matches each `<part id>` to the object id of a component, and reads `name` and `extruder` metadata from the object and each part. Write every object as a parent with components, even a single part. The G-code's `EXCLUDE_OBJECT_DEFINE NAME=` lines show the names Flash Studio loaded, so this can be checked without the window.
14. **Filament settings are one value per toolhead.** With N filaments, `filament_settings_id`, `filament_colour` and every filament key have N entries, and `different_settings_to_system` and `inherits_group` have N + 2 (process, each filament, printer).
15. **`filament used [g]` in the G-code is per toolhead.** Sum it for the total.
5. **Speed is best limited through `filament_max_volumetric_speed`**, not individual speed settings. It covers every move.
6. **Curly quotes.** Two commands failed because the chat app changed `"` to typographic quotes. The terminal command takes hyphenated words (`--filament ender-pla`) for this reason.

## 4. Lessons on approach

From this session and from the article the owner shared (How-To Geek, "This is what AI coding actually excels at", 2026), in which Codex got a Creator 5 Pro working with Orca Slicer and Home Assistant in a few tries:

- **Start from known working source and adapt it.** The article's author credits this for the result. The first session wrote a server from scratch against OrcaSlicer's source when the owner runs Flash Studio. Reading Flash Studio's own code is what finally produced a working project file, in minutes.
- **Run where the software runs.** That agent ran on the author's Mac and could test its own work. The first session could not, and should have said so at the start and handed over to Claude Code.
- **Agree the end state before building.** The owner wanted "open in the slicer, ready to print". The session built "produce G-code" and only found the gap at the last step.
- **Check facts that are checkable.** The controller's dimensions were on a page already fetched; the hook was designed from a guess and redrawn twice. The spool's limits were on its label.
- **Do not build new software to do a job once.** If the owner asks to print a part, get the part printing, then offer the tooling.
- **Say what you cannot do, early and once.** Then pick the route that works, even if it is not the one first proposed.

## 5. What to do next, in order

Done in session 2: the MCP path writes and opens the project without prompting (server instructions and `open -n`); the integration tests (`tests/test_real_slicer.py`); the filament colour from the request; the spool register; object names; multi-colour and multi-object 3MFs; a `best` quality level; the LAN upload and USB copy removed.

1. **Open the projects on screen** once the current print has finished: `~/Creator5Prints/20261115_173203_drzak_na_ps5_ovladac_v3/drzak_na_ps5_ovladac_v3.3mf` and `~/Creator5Prints/20261115_172949_Skeleton/Skeleton.3mf`. Confirm a new window with no dialog, the model on the plate under its own name, the presets shown as modified, supports on, four filaments in the right colours on the second, and Slice and Print enabled.
2. **Get the PS5 holder print result from the owner** and record it in `docs/SETTINGS.md`: first layer, walls, support removal, strength, fit. Fix any setting in `recommend.py` so it applies to every future print.
3. **Calibrate per spool.** The flow cap comes from the label's top speed, which is conservative. One flow and temperature test per spool, stored in the register, would replace it.
4. **STEP files.** Not supported (section 3, item 12). Ask the owner whether to add a converter library.
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
