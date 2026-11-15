# creator5-slicer-mcp

An MCP server that lets Claude, or any MCP client, slice 3D models for the **Flashforge Creator 5** with OrcaSlicer, choosing sensible quality, strength and filament settings for each job.

Say what the part is for and what filament is loaded. The server measures the model, picks the Creator 5 presets, adjusts walls, infill, shells, supports and brim, runs the slicer, and returns the G-code with a time and filament estimate and the reasoning behind each choice.

It is a thin layer over the slicer's own command line. It is not a fork of OrcaSlicer, so there is no C++ to build and slicer updates need no merging.

> **Status: early.** Confirmed with Flash Studio 1.7 on macOS: choosing settings, slicing, and opening the result as a project that Flash Studio slices and sends to the printer. A finished print from these settings has not yet been assessed. `send_to_printer` over the LAN is untested; print from Flash Studio instead. See [docs/HANDOVER.md](docs/HANDOVER.md).

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
      "args": ["--from", "git+https://github.com/rsutcliffe/creator5-slicer-mcp", "creator5-slicer-mcp"],
      "env": {
        "C5_PRINTER_IP": "192.168.1.50",
        "C5_PRINTER_SERIAL": "your-serial-number",
        "C5_PRINTER_CHECK_CODE": "your-check-code"
      }
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

The three `C5_PRINTER_*` values are only needed for sending files to the printer. Leave the `env` block out to slice only.

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
| `C5_OUTPUT_DIR` | Where jobs are written | `~/Creator5Prints` |
| `C5_PRINTER_IP` | Printer address on your network | none |
| `C5_PRINTER_SERIAL` | Printer serial number | none |
| `C5_PRINTER_CHECK_CODE` | LAN check code shown on the printer | none |

## Tools

| Tool | What it does |
|---|---|
| `check_setup` | Confirms the slicer, the Creator 5 presets and the printer details are in place |
| `list_filaments` | Lists the filament types understood and the presets installed |
| `analyse_model` | Measures an STL, OBJ or 3MF: size, volume, bed contact, overhangs, fit on the 256 mm bed |
| `recommend_settings` | Returns the chosen presets and settings with reasons, without slicing |
| `slice_model` | Analyses, chooses settings, writes the project, slices it, and confirms each setting reached the G-code |
| `open_in_slicer` | Opens the project in a new Flash Studio window, ready for Slice and Print |
| `printer_status` | Reads the printer's state over the local network (experimental) |
| `send_to_printer` | Uploads a sliced file; starts the print only when asked (experimental) |

### The main inputs

- **filament**: plain words, such as `black PLA`, `PETG`, `silk PLA`, `TPU 95A`, `ASA`
- **quality**: `draft`, `standard` or `fine`
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
Output      ~/Creator5Prints/20261115_141500_controller_holder/plate_1.gcode
```

## From the terminal

The same engine without an MCP client. No quotation marks are needed.

```bash
.venv/bin/python -m creator5_mcp.cli MODEL.stl --filament ender-pla --strength functional --copies 2 --usb
```

`--open` shows the layer preview, `--usb` copies the G-code to the USB stick that is plugged in, `--send` uploads it to the printer and `--print` uploads and starts. Save the printer's network details once with `--save-printer IP SERIAL CHECKCODE`.

## Printing the result

`slice_model` writes a project file (`project_3mf`) and `open_in_slicer` opens it in a new Flash Studio window with the model and every setting loaded. A new window leaves any project you already have open alone, and with an empty plate Flash Studio opens the file as a project without asking. Press Slice, then Print. This is the normal route.

The slicer's G-code preview is view-only, so do not open the `.gcode`. Two other routes exist but are not needed:

1. USB stick: copy the `.gcode` across (`--usb` does this) and start it from the printer's screen. Always works.
2. Network: `send_to_printer`, or `--send` / `--print`, uploads using the printer's LAN mode. Experimental. It only uploads unless told to start.

## Limits in this version

- Single-material jobs on toolhead 1. Multi-colour and multi-material plates are not yet handled.
- Models must be STL, OBJ or 3MF. Export STEP files as one of those.
- A 3MF with several objects is joined into one part; with several plates, only plate 1 is used.
- The recommended values are reasoned starting points, not measured optima. See [docs/SETTINGS.md](docs/SETTINGS.md).
- The LAN upload follows OrcaSlicer's Flashforge print host and has not been confirmed on hardware.

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
