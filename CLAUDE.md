# Working on creator5-slicer-mcp

Read `docs/HANDOVER.md` before changing anything. It records what is proven, what is not, and the mistakes that cost the owner an afternoon.

## What the owner wants

One sentence in, a print out. He names a model, a filament and what the part is for. Flash Studio opens with the model on the plate and every setting already right for his Creator 5 and that spool. He presses Slice and Print and walks away. No Terminal, no USB stick, no looking up printer details, no trial prints to find settings.

## Rules for this repo

1. **You are on the Mac. Use it.** Run the real slicer, open the real app, read the real output. Never ship a change you have not run here against Flash Studio.
2. **Adapt working source; do not invent.** Flash Studio is open source (`FlashForge/Orca-Flashforge`). When behaviour is unclear, read its code first. Every dead end in the first session came from guessing.
3. **The owner is not the test loop.** Do not ask him to paste output or try a command to see if it works. Test it yourself, then hand over something that works.
4. **Finish at "ready to print in Flash Studio".** A G-code file is not a finished result: Flash Studio only previews G-code. The deliverable is a project that opens in the Prepare tab.
5. **Settings come from evidence.** Printer presets from Flash Studio, temperatures and speed from the spool label, shape from the model. Check the sliced G-code to confirm they took effect before saying so.
6. **State confidence plainly, backed by what you checked.** Say what is proven and what is not. No padding, no hedging on things you have verified.
7. **Commands for the owner contain no quotation marks.** Chat apps turn straight quotes into curly ones and the shell rejects them.
8. **UK English. Short answers. Lead with the result.**

## Checks before you say "done"

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_test.py          # real slice with Flash Studio
```

Then open the resulting project in Flash Studio and confirm with your own eyes: model present, presets selected, changed values showing as modified, Slice and Print enabled.
