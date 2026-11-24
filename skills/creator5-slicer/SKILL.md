---
name: creator5-slicer
description: How to use the creator5-slicer MCP server to set up prints for a Flashforge Creator 5 in Flash Studio. Use when the person wants a model sliced, printed, checked or previewed for the Creator 5, a spool saved or tuned (flow, temperature, pressure advance), failed parts reprinted, two settings compared, or the printer's state read.
---

# Creator 5 slicer

The person names a model, a filament and what the part is for. The server chooses the settings, writes a
Flash Studio project, checks it, and opens it ready for Slice and Print. The person presses Print in
Flash Studio. The server never sends anything to the printer.

## The usual flow

1. Call `slice_model` with the model's path, the filament as the person said it, and a `quality` and
   `strength` that suit the part's purpose. A saved spool's name is enough for its filament.
2. Show the plate picture: call `preview` with the result's `project_3mf`.
3. Read the result back to the person in plain words (see "What the result says").
4. Call `open_in_slicer` with `project_3mf`, without asking.

Give a path the person named. Do not ask them to paste output or try a command: run the tools.

## Which tool

| The person wants | Tool |
| --- | --- |
| A model sliced and ready to print | `slice_model`, then `preview`, then `open_in_slicer` |
| To see the choices first, without slicing | `recommend_settings` |
| A model measured | `analyse_model` |
| A picture of the plate or of one layer | `preview` (`zoom`, `view`, `layer` or `z`) |
| To know whether stronger or finer is worth it | `compare_settings` |
| A spool remembered | `save_spool`; `list_filaments` shows what is saved |
| A spool's real flow limit | `flow_test`, then `record_flow_test` with the last good tube |
| The best temperature for a spool | `temperature_test`, then `record_temperature_test` with the best block |
| Sharp corners from a spool | `pressure_advance_test`, then `record_pressure_advance_test` |
| To say how a print came out | `record_print_result` |
| Only some parts of a plate reprinted | `reprint_objects` with the project and the parts' numbers |
| What they changed by hand in Flash Studio | `compare_with_saved_project` |
| What Flash Studio's own slice will print | `check_flash_studio_slice`, after they press Slice plate |
| What the printer is doing | `printer_status` |
| To check the installation | `check_setup` |

## What the result says

Relay every warning in `plan.warnings`. Then, from `slice_model`'s result:

- **`summary`**: print time and filament. `settings_mismatched` must be empty; anything in it did not
  reach the G-code.
- **`print_check`**: whether the shape will print. `checked: false` means nothing was judged and gives
  the reason: say so, and never call an unchecked part fine. `not_judged` lists what the file could not
  be measured for; mention it. `exact` is true only for a STEP.
- **`sliced_check`**: `mid_air` (plastic that starts with nothing under it), `left_out` (an object
  the G-code never prints), `smaller` (an outline with no line beside it), `stops_short_mm`.
- **`print_risks`**: parts likely to topple or come off the plate.
- **`designer_report`**: for a downloaded project, what was kept of the designer's setup and what was
  left behind.

## Things that catch people out

- **Flash Studio only previews a G-code file.** It cannot print one. Always open the project
  (`project_3mf`), never the `.gcode`.
- **A running server keeps its old code.** After an update the person must quit and reopen Claude.
  `check_setup` reports `restart_needed`.
- **Only toolhead 1 keeps a spool's own figures.** Flash Studio swaps a changed filament on toolheads
  2 to 4 for a stock preset. The plan warns what those toolheads will run at; for a multi-filament
  job, call `check_flash_studio_slice` after the person presses Slice plate.
- **One bed, one temperature.** The slicer heats it to the highest any filament asks for.
- **`auto_orient` only when asked.** Designers' files are usually turned for strength, and a model of
  several parts cannot be turned without merging them.
- **A pause needs the top of a layer.** With 0.2 mm layers, a pocket that ends at 2.8 mm needs
  `pause_before_z` 3.0.
- **A flow test is a plate of tubes, numbered from the front left**, rising left to right and then row
  by row to the back. The towers are numbered up from the bed.
- **`printer_status` is read-only**, and on stock firmware the printer answers only in LAN-only mode,
  which turns the Flashforge cloud off. Do not suggest that change lightly; say what it costs.
- **The print log is evidence.** Call `record_print_result` only for a print that really happened.
- **Commands for the person contain no quotation marks.** Chat apps turn them into curly ones and the
  shell rejects them.

## Models written as code

Where no model exists yet, a part can be written with build123d (the text-to-cad `cad` skill) and
exported as STEP, which `slice_model` takes. `models/rawlplug/build_rawlplug.py` in this server's
repository is a worked example.
