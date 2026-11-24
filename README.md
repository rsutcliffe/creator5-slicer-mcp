# creator5-slicer-mcp

An MCP server that lets Claude, or any MCP client, set up 3D models for the **Flashforge Creator 5** in Flash Studio, choosing sensible quality, strength and filament settings for each job.

Say what the part is for and what filament is loaded. The server measures the model, picks the Creator 5 presets, adjusts walls, infill, shells, supports and brim, and writes a project that opens in Flash Studio ready for Slice and Print. It slices that project itself first, to give a time and filament estimate and to confirm every chosen setting took effect.

It is a thin layer over the slicer's own command line. It is not a fork of OrcaSlicer, so there is no C++ to build and slicer updates need no merging.

> **Status: early.** Confirmed with Flash Studio 1.7 on macOS: choosing settings, writing the project, slicing it with Flash Studio's command line for single and four-colour models, and opening it in Flash Studio ready to slice. One print has been reported, with good quality. See [docs/HANDOVER.md](docs/HANDOVER.md).

## What you need

- macOS, Windows or Linux
- Python 3.10 or newer
- Flashforge's Flash Studio (formerly Orca-Flashforge), or an [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) build whose Flashforge presets are plain JSON files. OrcaSlicer 2.5 packs its presets into `.opc` files, which this server cannot read; with both installed, Flash Studio is used
- A Flashforge Creator 5 or Creator 5 Pro

## Install

With [uv](https://docs.astral.sh/uv/) there is nothing to install by hand. Add this to your MCP client's configuration. For Claude Desktop that is `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "creator5-slicer": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rsutcliffe/creator5-slicer-mcp", "creator5-slicer-mcp"]
    }
  }
}
```

For Claude Code:

```bash
claude mcp add creator5-slicer -- uvx --from git+https://github.com/rsutcliffe/creator5-slicer-mcp creator5-slicer-mcp
```

Or install it yourself and point the client at the `creator5-slicer-mcp` command:

```bash
pipx install git+https://github.com/rsutcliffe/creator5-slicer-mcp
```

### Claude Desktop bundle

Each release carries `creator5-slicer-mcp-<version>.mcpb`. Open it in Claude Desktop and it installs the server; Claude Desktop fetches the Python packages itself. Build one from a clone with `python scripts/build_bundle.py`.

### Check or remove an installation

```bash
creator5-slicer-mcp doctor
```

It checks the slicer, the presets, the prints folder, the print check tool, and which copy Claude Desktop and Claude Code run, and prints the fix beside each fault.

```bash
creator5-slicer-mcp uninstall
```

It disconnects the server from Claude Desktop and Claude Code. Your projects, print log and spools are left alone.

### Install from a local clone

Use this route if you want to edit the settings tables. None of these commands need quotation marks.

```bash
git clone https://github.com/rsutcliffe/creator5-slicer-mcp
cd creator5-slicer-mcp
./scripts/setup.sh
```

`setup.sh` does everything in one go and is safe to run again to update: it pulls the latest code, installs it into `.venv`, slices a test cube with your real slicer, and only if that passes registers the server with Claude Desktop and Claude Code. The full output is saved to `setup_report.txt`.

The individual steps, if you prefer to run them by hand:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/install_claude_desktop.py
```

`smoke_test.py` slices a 20 mm cube with your real slicer and prints the result. If it fails, the message names the log file to look at.

`install_claude_desktop.py` adds the server to Claude Desktop's configuration. It keeps your existing settings, saves a backup first, and is safe to run again. Quit and reopen Claude Desktop afterwards.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `ORCA_SLICER_PATH` | Slicer executable | found automatically: the first of OrcaSlicer, Flash Studio or Orca-Flashforge with readable presets |
| `ORCA_PROFILES_DIR` | Folder containing `Flashforge/` and `OrcaFilamentLibrary/` | the slicer's bundled `profiles` folder |
| `C5_OUTPUT_DIR` | Where jobs and the spool register (`spools.json`) are written | `~/Creator5Prints` |

## Tools

| Tool | What it does |
|---|---|
| `check_setup` | Confirms the slicer and the Creator 5 presets are in place, and reports the server's version, the git commit it started from, whether the code has changed since (`restart_needed`), and Flash Studio's release against the one last tested |
| `list_filaments` | Lists the filament types understood, the presets installed and the saved spools |
| `save_spool` | Remembers a spool and its label figures under a name |
| `record_print_result` | Records how a print came out, with its settings and anything changed by hand |
| `compare_with_saved_project` | Shows what you changed in Flash Studio, against the project the server wrote |
| `analyse_model` | Measures an STL, OBJ, 3MF or STEP: size, volume, bed contact, overhangs, parts, toolheads used, fit on the 256 mm bed, and the print check |
| `preview` | Returns a picture: the plate with every part in its toolhead's colour, or one sliced layer from above |
| `printer_status` | Reads the printer's state, progress, temperatures and loaded filament over the home network. Read-only |
| `reprint_objects` | Writes a project of only the named objects of a sliced plate, with a brim, to reprint parts that failed |
| `compare_settings` | Slices a model twice with two levels of quality or strength and gives the time, filament and settings that differ |
| `flow_test` | Writes a test print of tubes at rising flow, to find a spool's flow limit |
| `record_flow_test` | Saves the flow of the last good tube as the spool's limit |
| `recommend_settings` | Returns the chosen presets and settings with reasons, without slicing |
| `slice_model` | Analyses, chooses settings, writes the project, slices it, and confirms each setting reached the G-code |
| `open_in_slicer` | Opens the project in a new Flash Studio window, ready for Slice and Print |
| `check_flash_studio_slice` | After you press Slice plate in Flash Studio: reads what its window sliced (each toolhead's preset and temperature, the bed, pauses, time) and lists anything that differs from the project |

### The main inputs

- **filament**: the filament in toolhead 1, in plain words, such as `black PLA`, `PETG`, `silk PLA`, `TPU 95A`, `ASA`, or the name of a saved spool
- **other_filaments**: the filaments in toolheads 2, 3 and 4, for a multi-colour model
- **quality**: `draft`, `standard`, `fine` or `best`
- **strength**: `decorative`, `standard`, `functional` or `structural`
- **nozzle**: `0.25`, `0.4`, `0.6` or `0.8`
- **supports**, **brim**: `auto`, `on` or `off`
- **finish**: `standard` or `smooth`. Smooth uses at least fine layers and irons flat tops
- **nozzle_temp**, **bed_temp**, **max_speed**: figures from the spool's label, for filament from other makers

Anything else in OrcaSlicer is reachable through `process_overrides` and `filament_overrides`.

[docs/SETTINGS.md](docs/SETTINGS.md) lists every value the server sets and the reasoning.

## Example

> Slice ~/Models/controller_holder.stl in black PLA. It is a wall bracket that carries a game controller, so make it strong. Two copies.

Claude calls `slice_model` with `filament="black PLA"`, `strength="functional"`, `copies=2`, and reports back something like:

```
Preset      0.20mm Standard @FF C5, Flashforge PLA Basic @FF C5
Walls       4        Infill  25% gyroid
Top/bottom  6 / 5 layers
Supports    off (no significant overhangs found)
Project     ~/Creator5Prints/20261115_141500_controller_holder/controller_holder.3mf
```

## Spools

Save a spool once with the figures from its label, and its name is enough afterwards:

```bash
.venv/bin/python -m creator5_mcp.cli --save-spool black-pla --filament ender-pla --nozzle-temp 215 --bed-temp 60 --max-speed 80 --colour black
```

A request for `the black PLA` then uses that material, those temperatures, that speed limit and that colour. A figure given with a request beats the register. The register is `spools.json` in the output folder.

Once a spool has printed well, or you have run a flow test, add `--max-flow` with the flow in mm³/s. That proven limit is then used at every layer height. Without it the cap is worked out from the label's top speed, which halves at half the layer height and makes fine prints slower than they need to be.

## Learning from prints

When a print is done, say how it went. `record_print_result` keeps the outcome with the project's presets and changed settings in `print_results.json` in the output folder. If you edited the project in Flash Studio and saved it, pass that file too: what you changed by hand (supports switched on, a part cloned, the model swapped) is recorded alongside. Settings changes can then point at real prints.

```bash
.venv/bin/python -m creator5_mcp.cli --record-result PROJECT.3mf --outcome good --notes clean walls, supports came off easily
```

## Pictures of the result

Every `slice_model` result has `plate_png` and `first_layer_png`: the plate as the project file holds it, each part in its toolhead's colour, and the first sliced layer from above with supports, brim and skirt drawn paler. The `preview` tool returns either picture to the chat, the plate from an angle or from above, and any layer by number or height.

The plate is drawn from the project, so it shows what Flash Studio will open, with colour-painted faces in the colours they were painted. When the parts cover under a quarter of the bed the picture frames them; `preview` takes `zoom` to choose. A layer is drawn from the G-code, so it shows what will print.

## Printer status

`printer_status` reads what the Creator 5 is doing over the home network: its state, the file printing with progress and time left, each toolhead's temperature and the bed's, and the material and colour loaded in each toolhead. It is read-only: the server asks the printer one question and can send it nothing else.

- The printer is found by itself; no address is entered.
- On stock firmware the printer answers only in **LAN-only mode**, set on its network screen. LAN-only mode turns the Flashforge cloud off, so the phone app and cloud printing stop until it is turned back. In cloud mode the tool still finds the printer and says so.
- The printer asks for its **check code**, shown on the same screen. Store it once in the Mac's Keychain with the command the tool prints; it asks for the code, and the code is never written to a file or shown in a reply. To remove it: `security delete-generic-password -s creator5-slicer-mcp-printer -a SERIAL`.
- Anyone with the check code on your network could control the printer. Change it on the printer if it has been shared.
- macOS only for now. Not yet proven against a real printer: see the handover.

## Print risks

`slice_model` returns `print_risks`: what may stop a sound part finishing. A part that is tall on a narrow footing, a part resting on little of the plate with the brim off, a filament whose preset has no temperature for the textured plate, and several tall parts sharing a long print. Each warning names the object. The thresholds are starting points, not measured on the Creator 5.

## The sliced check

`slice_model` also returns `sliced_check`, which compares the G-code with the model, from the toolpath and not the settings:

- **Starts in mid-air:** plastic with nothing printed under it that joins nothing on its own layer, with the object, height and place. Overhangs and bridges are joined to the part, so they are left to the print check.
- **Left out:** an object in the project that the G-code never prints.
- **Printed smaller than modelled:** part of an object's outline, or of one toolhead's parts, has no line beside it. This catches a part printed by the wrong toolhead and detail the slicer dropped, down to about 0.3 mm (outlines are compared on a 0.125 mm grid).
- **Stops short:** the last layer ends below the model's top.

Findings join the plan's warnings as `Sliced check: ...`. A mesh that is open at a height cannot be compared, and `not_judged` says so.

## The print check

`slice_model`, `recommend_settings` and `analyse_model` each return `print_check`: whether the shape will print on an FDM printer, before anything is sliced. It measures walls against two lines of the nozzle, overhangs and bridges, knife edges, holes, gaps and trapped volumes, and compares how the part stands with the other ways it could lie. Errors and warnings are added to the plan's warnings, each with the measured figure, the limit, where it is and whose limit it is.

- `not_judged` lists what the file could not be measured for. A mesh (STL, 3MF) is sampled and holds no holes, pins or lettering to judge; a STEP is measured exactly (`exact` is true).
- An exact check that takes longer than 60 seconds gives way to a check of a mesh of the same STEP, and the result says so.
- A file of several parts is checked a part at a time inside 30 seconds. A part that is not reached, such as lettering made of many small bodies, is named in `not_judged`.
- A part that stands another way up with much less support gets a warning naming the rotation. Nothing is turned unless `auto_orient` is passed.
- `checked: false` means nothing was judged, with the reason. It is never a pass. OBJ files are not checked.

The check is the `cad` command from [text-to-cad](https://github.com/earthtojake/text-to-cad) (MIT), which `scripts/setup.sh` installs with `uv tool install cad` and sets to send no usage stats and not to update itself. Without it, models are sliced as before and `print_check` says the check was skipped. Its limits are general design-guide figures, not measurements from the Creator 5.

## Models written as code

`models/dualsense_hook/build_hook.py` is a part written with [build123d](https://github.com/gumyr/build123d): named dimensions at the top, an exact solid, a STEP file out. Change a number, run `cad run build_hook.py` in that folder (with `cad server start` before and `cad server stop` after), and give the STEP to `slice_model`.

## Flow test

`flow_test` writes a short test print that finds a spool's real flow limit: how much plastic it can melt each second. The plate holds one tube for each flow, 5 to 25 mm³/s by default, slowest at the front left, rising left to right and then row by row to the back. Print it, find the last tube whose wall is smooth and full, and give its number to `record_flow_test`, which saves that flow as the spool's limit. Nine tubes take about 20 minutes and 14 g.

Each tube's flow is checked in the G-code before the project is handed over. Nothing is changed on the printer. The test itself has not been printed yet.

## Reprinting parts that failed

When some parts of a plate fail, `reprint_objects` takes the project and the numbers of those parts and writes a new project with them alone: the same presets, changed settings and pause, plus a 5 mm brim. `record_print_result` lists the numbers when a plate of several is reported as failed.

## Is stronger or finer worth it?

`compare_settings` slices one model twice, with two levels of quality or strength, and returns each slice's time, filament and layers, the difference, and every setting that differs. Both projects are kept.

## Pausing to embed something

`pause_before_z` (or `--pause-before-z`) pauses the print before the layer whose top is at that height, using the printer's own pause command, so a magnet, nut or NFC tag can go into a pocket. The server confirms the pause in the G-code and reports it as `pauses_at_z`. Pick a height that is the top of a layer: with 0.2 mm layers and a 0.2 mm first layer, a pocket that ends at 2.8 mm needs `pause_before_z` 3.0.

## Multi-colour models

The Creator 5 has four toolheads. A 3MF that assigns its parts to different filaments keeps its objects, its parts and those assignments, whether it was saved by Bambu Studio, OrcaSlicer or Flash Studio, or is a plain 3MF with colours as base materials. Colour painting on a Bambu or Orca model is kept too. A CAD export that names the toolhead only in a part number (`Logo_Black_Extruder2`) is read as well, and separate items whose boxes overlap, such as a logo set into a body, are joined into one object so they cannot be moved apart.

`models/two_colour_test/build_test.py` writes a small two-colour test piece (two blocks side by side, about 12 minutes and 4 g). Print it before a long multi-colour job to prove toolhead changes and the purge tower.

Name the filament in each toolhead: `filament` for toolhead 1, `other_filaments` for the rest. If you name fewer than the model uses, the others get toolhead 1's filament in the model's own colours, and a warning lists which colour to load where. The purge tower is switched on for these jobs.

## From the terminal

The same engine without an MCP client. No quotation marks are needed.

```bash
.venv/bin/python -m creator5_mcp.cli MODEL.3mf --filament black-pla --quality best --strength functional --open
```

```bash
.venv/bin/python -m creator5_mcp.cli FIGURE.3mf --filament black-pla --also white-pla --also pla --also gold-silk-pla --open
```

`--open` opens the project in Flash Studio. `--also` names the filament in the next toolhead.

## Printing the result

`slice_model` writes a project file (`project_3mf`) and `open_in_slicer` opens it in a new Flash Studio window with the model and every setting loaded. A new window leaves any project you already have open alone, and with an empty plate Flash Studio opens the file as a project without asking. Press Slice, then Print.

This server never sends anything to the printer. Flash Studio's own Print button does that.

## Limits in this version

- Models can be STL, OBJ, 3MF or STEP. STEP needs the optional `cascadio` library (about 70 MB), which `scripts/setup.sh` installs; with `pip`, install `creator5-slicer-mcp[step]`. Without it, a STEP file gets a message saying how to add it.
- From a 3MF with several plates, one plate is used: the first unless `plate` says otherwise. The result's `model.plates` says how many there are.
- A 3MF saved by a slicer comes with `designer_report`: the printer it was set up for, the shape settings kept and replaced, and the designer's other changes, which are not carried over.
- `auto_orient` works on single-part models only. Turning a model of several parts would merge them.
- Every toolhead shares one process. Where two materials want different process settings, the slower speed wins.
- Every toolhead shares one bed. The slicer heats it to the highest temperature any filament asks for; when filaments differ by 10 C or more, a warning says which toolhead that is too hot for.
- Detail thinner than two nozzle lines (0.8 mm on the 0.4 mm nozzle) prints soft. The server measures it and warns, and switches to variable-width walls, but it cannot make a 0.4 mm nozzle draw a 0.6 mm letter stroke cleanly.
- No setting smooths the underside: it copies the build plate.
- The fine detail and narrow gap measures sample dense meshes, so a very small patch can be missed. They look at upright walls only.
- The recommended values are reasoned starting points, not measured optima. See [docs/SETTINGS.md](docs/SETTINGS.md).

## Development

```bash
pip install -e ".[dev]"
pytest
# also check every filament type against real OrcaSlicer presets:
ORCA_TEST_PROFILES_DIR=/path/to/OrcaSlicer/resources/profiles pytest
```

## Licence and credits

MIT. See [LICENSE](LICENSE).

The pictures are drawn with code adapted from [unofficial-bambu-mcp](https://github.com/danielefinocchiaro/unofficial-bambu-mcp) (MIT); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Printer status follows the protocol documented by the community at [flashforge-api-docs](https://gitlab.com/parallel-7/flashforge-api-docs); no code is taken from it.

The print check runs text-to-cad's `cad` command as a separate program; it contains no text-to-cad code. text-to-cad is MIT-licensed.

This project runs OrcaSlicer as a separate program and reads the preset files installed with it; it contains no OrcaSlicer code. OrcaSlicer is developed by the OrcaSlicer contributors under AGPL-3.0. Flashforge and Creator 5 are trademarks of their owner. This project is independent and is not endorsed by either.
