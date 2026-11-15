# creator5-slicer-mcp

An MCP server that lets Claude, or any MCP client, slice 3D models for the **Flashforge Creator 5** with OrcaSlicer, choosing sensible quality, strength and filament settings for each job.

Say what the part is for and what filament is loaded. The server measures the model, picks the Creator 5 presets, adjusts walls, infill, shells, supports and brim, runs the slicer, and returns the G-code with a time and filament estimate and the reasoning behind each choice.

It is a thin layer over the slicer's own command line. It is not a fork of OrcaSlicer, so there is no C++ to build and slicer updates need no merging.

> **Status: early.** The settings logic and the server are covered by automated tests, including a check of every filament type against OrcaSlicer's real Creator 5 presets. Slicing against a real OrcaSlicer install and uploading to a real printer have not yet been confirmed. Run `scripts/smoke_test.py` after installing, and treat `send_to_printer` as experimental.

## What you need

- macOS, Windows or Linux
- Python 3.10 or newer
- [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) with its Flashforge profiles, or Flashforge's own Flash Studio / Orca-Flashforge, which is built on OrcaSlicer and shares its command line
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

### Check it works

```bash
git clone https://github.com/rsutcliffe/creator5-slicer-mcp
cd creator5-slicer-mcp
pip install -e .
python scripts/smoke_test.py
```

This slices a 20 mm cube with your real slicer and prints the result. If it fails, the message names the log file to look at.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `ORCA_SLICER_PATH` | Slicer executable | found automatically; on macOS `/Applications/OrcaSlicer.app`, then Flash Studio |
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
| `analyse_model` | Measures an STL: size, volume, bed contact, overhangs, fit on the 256 mm bed |
| `recommend_settings` | Returns the chosen presets and settings with reasons, without slicing |
| `slice_model` | Analyses, chooses settings, slices, and returns G-code, a 3MF project and estimates |
| `open_in_slicer` | Opens the result in the slicer window to inspect and print from there |
| `printer_status` | Reads the printer's state over the local network (experimental) |
| `send_to_printer` | Uploads a sliced file; starts the print only when asked (experimental) |

### The main inputs

- **filament**: plain words, such as `black PLA`, `PETG`, `silk PLA`, `TPU 95A`, `ASA`
- **quality**: `draft`, `standard` or `fine`
- **strength**: `decorative`, `standard`, `functional` or `structural`
- **nozzle**: `0.25`, `0.4`, `0.6` or `0.8`
- **supports**, **brim**: `auto`, `on` or `off`

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

## Printing the result

Three routes, safest first:

1. `open_in_slicer` opens the 3MF project in OrcaSlicer or Flash Studio. Look over the preview and print from there as usual.
2. Copy the `.gcode` to a USB stick.
3. `send_to_printer` uploads over your network using the printer's LAN mode. It only uploads by default. Pass `start_print=true` once the bed is clear and the filament is loaded.

## Limits in this version

- Single-material jobs on toolhead 1. Multi-colour and multi-material plates are not yet handled.
- Shape analysis reads STL only. 3MF, STEP and OBJ still slice, with supports off unless you ask for them.
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
