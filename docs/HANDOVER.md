# Handover: Creator 5 slicing by MCP

Written 15 November 2026 at the end of the first session. That session ran in a cloud chat with no ability to run software on the owner's Mac. You, Claude Code on the Mac, do not have that limit.

## 1. Where things stand

The chain works end to end and the first print has been sent:

```
model + filament + purpose
  -> settings chosen (presets + overrides)
  -> G-code sliced by Flash Studio's command line (used for time, weight and checks)
  -> project .3mf written by this server
  -> opened in Flash Studio, Slice, Print over Flash Studio's own printer link
```

Proven on the owner's Mac:

| Step | Evidence |
|---|---|
| Preset lookup and flattening against Flash Studio's presets | All 156 Creator 5 filament presets resolve to 1.75 mm |
| Slicing through Flash Studio's CLI | Clean exit, complete G-code, sensible time and weight |
| Settings reach the G-code | Flow, speed, temperatures, walls and infill read back from the file |
| Project file opens in Flash Studio with settings applied | Presets shown as modified; sliced; "Send completed" to Creator 5 |

Not yet proven:

- The finished print: surface quality, strength, and whether the hook fits the controller.
- The MCP tools called from Claude Desktop or Claude Code (only the terminal command has been used; both share `server.slice_model`).
- `send_to_printer` and `printer_status` over the LAN. Written from OrcaSlicer's source, never run against the printer. Flash Studio's own Print button makes them unnecessary for now.

## 2. The machine

- MacBook Pro, Apple Silicon, Python 3.14. Repo at `~/Code/creator5-slicer-mcp`, virtualenv in `.venv`, output in `~/Creator5Prints`.
- **Flash Studio** at `/Applications/Flash Studio.app`, bundle id `com.flashforge.orca-flashforge`. It is Orca-Flashforge 1.7, built on OrcaSlicer 2.3.0, and reports `SLIC3R_VERSION 01.10.01.50`. Intel build, runs under Rosetta. Its presets are plain JSON in `Contents/Resources/profiles`.
- **OrcaSlicer 2.5.0-rc** is also installed. Its presets are packed into `<vendor>.opc` files (magic `ZCRO`), which this server cannot read, so `config._pick` chooses Flash Studio.
- Printer: Flashforge Creator 5, 0.4 mm hardened nozzle, textured PEI plate, 256 mm cube build volume, already bound in Flash Studio.
- Filament in use: **Creality Ender PLA, black**. Label: 1.75 mm, nozzle 190 to 220 C, bed 25 to 60 C, speed 40 to 80 mm/s. The server knows it as `ender-pla`: nozzle 215, bed 60, flow capped at 6.7 mm3/s.

## 3. Things that were learned the hard way

Each of these cost a round trip through the owner. Do not relearn them.

1. **Same-named presets in two vendor folders.** Flash Studio has an old `fdm_filament_common` under `Flashforge/` (2.85 mm filament) and the current one under `OrcaFilamentLibrary/` (1.75 mm). A parent must be looked up in the same vendor folder as the preset naming it. Getting this wrong produced G-code with about 38% of the needed plastic. `slicer.slice_model` now refuses any filament that does not resolve to 1.75 mm.
2. **`--export-3mf` crashes Flash Studio's CLI on macOS.** It renders thumbnails through OpenGL with no window: "Invalid OpenGL version", exit code -11. The G-code is written before the crash. Do not use that flag.
3. **Flash Studio will not print a G-code file.** Opening one gives a view-only preview with Slice and Print disabled. Open a project instead.
4. **The project format** (`project.py`). Flash Studio accepts a .3mf with plain inline geometry plus `Metadata/project_settings.config`, provided:
   - `3D/3dmodel.model` carries `<metadata name="Application">BambuStudio-01.10.01.50</metadata>`;
   - the settings name the system presets in `printer_settings_id`, `print_settings_id`, `filament_settings_id`;
   - `filament_colour` has one entry per filament (the loader counts filaments from it);
   - `inherits_group` is `["", "", ""]` and `different_settings_to_system` lists, per preset, the keys that differ, joined by `;`, in the order process, filament, printer.
   On load, any key not in that list is reset to the system preset's value, and the preset is shown as modified. Source: `PresetBundle::load_config_file_config` and `PresetCollection::load_external_preset` in `FlashForge/Orca-Flashforge`.
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

1. **Get the print result from the owner** and act on it: first layer, wall finish, ironed face, strength, fit on the controller. Record what worked in `docs/SETTINGS.md`. If anything is off, fix the setting in `recommend.py` so it applies to every future print.
2. **Make the MCP path as smooth as the terminal one.** In Claude Code and Claude Desktop, "slice X in Y for Z" should call `slice_model`, then `open_in_slicer` with `project_3mf`, without prompting. Test it for real. Check whether Flash Studio shows an "open as project or import geometry" dialog and, if so, whether its `import_project_action` setting removes it.
3. **Add an integration test** that runs on this Mac only: slice a cube with the real Flash Studio, write the project, and assert the G-code carries the overrides. Mark it so it skips elsewhere.
4. **Keep a spool register.** A small JSON file of the owner's filaments with their label figures, so "the black PLA" resolves without a photo. Accept label figures when a new spool appears.
5. **Tidy the project file.** Add `Metadata/model_settings.config` for object names and plate assignment, and set the filament colour from the request.
6. **Multi-material.** The Creator 5 has four toolheads. The server handles one filament on toolhead 1.
7. **Decide the fate of the LAN upload and the USB copy.** The owner does not want either. Remove them or leave them clearly marked as unused.
8. **OrcaSlicer 2.5 `.opc` presets.** Only worth reading if the owner moves off Flash Studio.

## 6. The hook, for reference

`models/dualsense_hook/build_hook.py` generates the v1.3 DualSense wall hook now printing. It is lofted from cross-sections and prints with the back plate on the bed, no supports. Dimensions were scaled from the drawing at dimensions.com/element/playstation-5-dualsense-controller (160 mm overall width):

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
