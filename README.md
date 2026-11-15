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
| `check_setup` | Confirms the slicer and the Creator 5 presets are in place, and reports the server's version, the git commit it started from, and whether the code has changed since (`restart_needed`) |
| `list_filaments` | Lists the filament types understood, the presets installed and the saved spools |
| `save_spool` | Remembers a spool and its label figures under a name |
| `record_print_result` | Records how a print came out, with its settings and anything changed by hand |
| `compare_with_saved_project` | Shows what you changed in Flash Studio, against the project the server wrote |
| `analyse_model` | Measures an STL, OBJ, 3MF or STEP: size, volume, bed contact, overhangs, parts, toolheads used, fit on the 256 mm bed |
| `recommend_settings` | Returns the chosen presets and settings with reasons, without slicing |
| `slice_model` | Analyses, chooses settings, writes the project, slices it, and confirms each setting reached the G-code |
| `open_in_slicer` | Opens the project in a new Flash Studio window, ready for Slice and Print |

### The main inputs

- **filament**: the filament in toolhead 1, in plain words, such as `black PLA`, `PETG`, `silk PLA`, `TPU 95A`, `ASA`, or the name of a saved spool
- **other_filaments**: the filaments in toolheads 2, 3 and 4, for a multi-colour model
- **quality**: `draft`, `standard`, `fine` or `best`
- **strength**: `decorative`, `standard`, `functional` or `structural`
- **nozzle**: `0.25`, `0.4`, `0.6` or `0.8`
- **supports**, **brim**: `auto`, `on` or `off`
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

## Multi-colour models

The Creator 5 has four toolheads. A 3MF that assigns its parts to different filaments keeps its objects, its parts and those assignments, whether it was saved by Bambu Studio, OrcaSlicer or Flash Studio, or is a plain 3MF with colours as base materials. Colour painting on a Bambu or Orca model is kept too.

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
- From a 3MF with several plates, only plate 1 is used.
- `auto_orient` works on single-part models only. Turning a model of several parts would merge them.
- Every toolhead shares one process. Where two materials want different process settings, the slower speed wins.
- Every toolhead shares one bed. The slicer heats it to the highest temperature any filament asks for; when filaments differ by 10 C or more, a warning says which toolhead that is too hot for.
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

This project runs OrcaSlicer as a separate program and reads the preset files installed with it; it contains no OrcaSlicer code. OrcaSlicer is developed by the OrcaSlicer contributors under AGPL-3.0. Flashforge and Creator 5 are trademarks of their owner. This project is independent and is not endorsed by either.
