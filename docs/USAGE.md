# Using AYFX Workbench

## Audition and collect effects

- **Source Banks** opens with the bundled collections. Choose a bank from the selector or use **Open source…** to open another AFB. Click an effect to play it and show its frames.
- **Add to My Bank** appends the selected source effect. Drag an effect into My Bank to insert it at a specific position.
- Click an effect in **My Bank** to play it. Press **Space** or click **Play working** to replay the selected working effect. Right-click in the working frame grid to replay it while keeping the frame selection. Press **Esc** to stop playback.
- The **My Bank** and **Source** frame tabs switch the frame view silently. Drag the dividers to resize the panes.

## Arrange and save My Bank

- Use **New effect** to start with one silent frame. Drag effects within My Bank or use **Move up** and **Move down** to change their order. The three-digit numbers are zero-based effect indices and change when you reorder the bank.
- With My Bank focused, **Ctrl+C** copies the whole effect and **Ctrl+V** pastes it after the selection. **Ctrl+D** duplicates, **F2** renames, and **Delete** removes the selected effect.
- Each row shows the effect's contribution to the saved AFB, including its offset and any name. Hover over a row for the breakdown. The bank total also includes the one-byte header.
- **File → Open working bank…** loads a bank for editing. **Save bank** writes to the current path. **Save bank as…** chooses a new path and makes it the current path. An asterisk in the title marks unsaved changes. A bank needs at least one effect to save.

## Edit frames

- The **My Bank** tab lets you edit the selected working effect. In the **Source** tab you can inspect and copy frames.
- Toggle the **T** or **N** checkbox to enable tone or noise. Double-click a hex value to type it. **Per** is the tone period, **Ns** is the noise period, and **V** is the volume.
- Hold the left mouse button and drag across rows in a **Period**, **Noise**, or **Volume** bar column to draw values. The hex values update as you draw. Hold the pointer near the top or bottom edge to scroll during a stroke.
- Click a frame number and **Shift-click** another to select a range. With the frame grid focused, **Ctrl+C** copies the selected frames from either tab. In My Bank, **Ctrl+V** inserts them before the focused frame. Use the **Frames** menu or the buttons to insert, append, or delete frames.
- **Ctrl+Z** undoes a bank or frame edit. One drag stroke is one undo step. Use **Bank → Redo** to reapply it.
- Each frame lasts **20 ms**. A sequence of 50 frames lasts one second.
