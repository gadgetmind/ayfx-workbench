# AYFX Workbench internals

## Run and package

Follow the [Ubuntu setup in the README](../README.md). Run `python app.py` from the venv. The application runs directly from the Python sources and uses PySide6 for the interface and audio output.

Keep `docs/USAGE.md` beside the application sources in the release tree. The Help dialog reads that Markdown file at runtime. The default source banks live in `samples/`, also relative to `app.py`. The command-line options can select a working bank or a different source directory.

## Code map

| File | Responsibility |
|---|---|
| `app.py` | Command-line parsing, Qt application, source and working bank views, drag and drop, bank operations, file dialogs, and playback dispatch. |
| `ayfx.py` | Immutable `Frame` and `Effect` values, AFB offset handling, frame encoding and decoding, NexySFX title extraction, and byte counts. |
| `frames.py` | Qt frame model and editor, numeric entry, tone and noise switches, bar drawing, range selection, and frame clipboard. |
| `synth.py` | AY tone and noise synthesis from decoded frames and conversion to PCM. |

## Follow an effect through the application

1. **Load.** `decode_bank` reads the effect offsets and calls `decode_effect`. Each `Effect` holds the encoded stream, a tuple of decoded frames, and name bytes. Source banks and My Bank have separate lists in `Workbench`. Source effects can be shared with My Bank because effects and frames are immutable.
2. **Edit.** `FrameTable` shows the selected effect and emits the revised frames after an edit. A drag stroke emits once when it finishes. `Workbench.commit_frames` repacks those frames with `effect_from_frames` and pushes a `FrameChange` onto the Qt undo stack. Bank edits use `BankChange`. If you add an editor operation, send it through the undo stack so the working view and saved-state indicator follow it.
3. **Play.** `Player` submits a `RenderJob` to a single-thread Qt pool. `render_pcm` advances the AY tone and noise generators at a 1.7734 MHz chip clock and produces 44.1 kHz signed 16-bit mono PCM. Qt's `QAudioSink` plays it on the default output. A playback token lets the player discard a render that arrives after a later play or stop.
4. **Save.** `encode_bank` rebuilds the offset table and writes each effect stream with its optional name. `QSaveFile` commits the result. A successful commit updates the current path and marks the undo stack clean.

## AFB compatibility and byte counts

- The first byte gives the number of effects. A count byte of zero represents 256 effects. Each effect has a two-byte little-endian offset relative to the second byte of that offset entry.
- Each frame starts with a flags byte. Bits 0–3 hold volume. Bits 4 and 7 disable tone and noise. Bit 5 introduces a two-byte tone period and bit 6 introduces a one-byte noise period. Omitted periods carry over from the previous frame. A frame occupies one to four bytes.
- Standard effect streams end with `D0 20`. An effect can have a zero-terminated name after the marker. The Workbench preserves the stored stream of ordinary effects until their frames are edited. On NexySFX import it extracts the readable title from recognised metadata and saves standard AYFX effect records.
- The per-effect byte count includes its stream, end marker, optional name and terminator, and two-byte offset. The bank total adds the one-byte count. A standard saved bank contains 1–256 effects, with each relative offset fitting in 16 bits.
- The editor and audition renderer use 50 frames per second. The AFB holds the frames in order. The game playback routine determines how often to advance them.

## Release smoke test

From a fresh extraction, follow the README and run these commands:

```bash
python app.py -h
python app.py '-?'
python app.py --help
python app.py --unknown
python app.py --bank samples/tests.afb --samples samples --maximised
```

Check that all three help switches show the same usage. The invalid option should print usage plus the error and return 2. In the desktop app, press F1, audition a source effect, add it to My Bank, edit a frame and undo the edit. Save As to a temporary path, compare the file size with the displayed bank total, and reopen it. Use speakers or headphones to check auditioning on the release machine.

To package a release from a committed snapshot, run `git archive` on the release tag. For example, after committing the release files:

```bash
git tag v0.1.0
git archive --format=zip --prefix=AYFX_Workbench/ \
    --output=../AYFX_Workbench-v0.1.0.zip v0.1.0
```

The archive should contain the Python sources, requirements, README, licence notices, both documents, and the four sample banks listed in the README.
