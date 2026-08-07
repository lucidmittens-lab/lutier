# LUTier — Known Good Summary (2026-07-25)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-8.md` (kept alongside, not deleted). That
checkpoint closed out the docking migration as confirmed-working on real
hardware and flagged "UI polish/beautification" as the next backlog item,
scope not yet defined. This checkpoint covers that whole arc: a
fixed (no longer OS-theme-dependent) dark palette, five rounds of Tom's
direct color-value feedback on it, and a real bug (not just a color
tweak) found and fixed along the way. This is also the first checkpoint
written from a NEW chat session ("LUTier DEV v014 migration," picking up
from "v013 migration") -- see the Environment section below for what
that meant operationally, since it wasn't just a clean continuation.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT,
Stack Forking) non-destructively, preview against a Sample/Reference
image pair with a wipe compare, inspect via histogram/waveform/
vectorscope/Graph (3D point cloud + wireframe cage + 1D YRGB curves),
save named "Stack Memory" snapshots, maintain reusable LUT/CDL/Image
libraries, undo/redo Stack edits, save/load whole sessions (`.ltr`),
export the baked result to `.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp`
via a dedicated Export dialog, with every panel freely drag-to-rearrange/
tab/float. Packaged as a native macOS `.app` via py2app, icon included.
In-app manual via Help > Manual, also on disk as `USER_MANUAL.md`.

## What changed since #8 (RUNNING.md #62-#67)

**#62 -- fixed dark palette (no longer OS-theme-dependent).** Per Tom:
the app looked terrible in light-OS-mode screenshots; wanted it to stop
depending on the OS theme at all, not just tolerate a dark one.
`style.build_dark_palette()` now constructs a real `QPalette` with fixed
colors, applied via `app.setPalette(...)` before `app.setStyleSheet(...)`
in `main()` -- every `palette(...)` QSS reference resolves from this
fixed source now, regardless of OS light/dark setting. Panel headers got
a blue accent (title text + underline) per Tom's explicit ask; Stack
Forking's Branch B moved off `palette(link)` to amber so it wouldn't
collide with the new blue.

**#63 -- first round of exact color values.** Tom: panel gray darker
(`#3D3D3D`), accent darker/more muted (`#2C4066`), hint text a new color
(`#617191`). Also split a real "hint/muted text" role
(`QPalette.PlaceholderText`, `_HINT_TEXT`) out of `_DARK_MID` (which had
been doing double duty as both border color AND muted-text color).
**Note:** this entry's own verification claimed hint text "reads in the
new muted blue-gray" across the app -- that claim was WRONG, see #66
below; it was an eyeballed screenshot read, not a pixel-sampled one.

**#64 -- Viewer panel empty-space diagnostic + a real fragility fixed.**
Tom: the space around the Viewer panel's actual image looked lighter
than `#3D3D3D`. Pixel-sampled a real screenshot first -- every pixel was
ALREADY exactly `#3D3D3D`, so #63's values were never wrong. Root cause
was that `WipeCompareWidget.paintEvent` never explicitly painted its own
background (relied on transparency + whatever's underneath), which is
the same class of implicit-compositing assumption that caused two real
bugs earlier in this project's history (#44, #46). Hardened it
regardless: `paintEvent` now unconditionally fills its own rect with
`palette(base)` first. Verified with a deliberate letterbox-stress
screenshot (window resized so the panel is much taller than the image's
aspect ratio).

**#65 -- panel gray to `#2B2B2B`.** Tom, simple follow-up: "Maybe its
just my eyes. try this gray #2B2B2B." Straight value swap, pixel-
verified.

**#66 -- panel gray to `#242424` + header text = hint color + a real bug
found.** Two more asks: darker gray again, and panel header text should
match hint text (keeping the accent as the underline only, not the
label color). Implementing the second one is what surfaced the real
bug: pixel-sampling the new header-text rule showed it rendering as
plain text color, not hint color -- and checking further, EVERY existing
`palette(placeholder-text)` usage in the app (the #63 hint labels,
disabled button/menu text, every panel module's own hint labels) was
ALSO wrong the same way, confirming #63's "confirmed working" claim was
never actually pixel-verified. Root cause (isolated repro, reproduced
under both `offscreen` and `minimal` Qt platform plugins, so not a
sandbox-only artifact): `QApplication.palette()` correctly holds the
custom `PlaceholderText` color for the app's whole lifetime, but
`MainWindow.palette()` silently drops that ONE role back to Text color
once `.show()` triggers Qt's style-polish pass -- every other role
checked the same way survives polish fine. Fix: stopped routing hint
text through `palette(placeholder-text)` at all; `style.HINT_TEXT_HEX`
(public alias for `_HINT_TEXT`) is now interpolated as a literal hex
everywhere hint text is colored, in `style.py`'s own QSS and in four
panel modules' inline `setStyleSheet()` calls. `QPalette.PlaceholderText`
itself is left mapped to `_HINT_TEXT` -- still correct for genuine
native placeholder text (e.g. the Browser search box), a different,
actually-working mechanism.

**#67 -- four more values.** Panel gray to `#141414` (now literally
darker than `_DARK_WINDOW`, the space BETWEEN panels -- flagged, not
blocked on, since that inverts this file's original "cards lighter than
background" design language into "recessed panels" instead), header
text AND hint text both to `#BDBDBD` (one constant change covers both,
since header text already routes through `_HINT_TEXT`), and the actual
draggable bars between panels (`QMainWindow::separator`) to a new,
dedicated `#0D0D0D` (`_SEPARATOR_DIVIDER`, deliberately not a repoint of
`_DARK_MID`, which also colors unrelated structural borders). Tom's
response: **"This is what i was hoping for."** -- current color state
confirmed as the target.

**A note on aces_drt_panel.py / lut_controls_panel.py:** #63's own entry
listed these two files as updated alongside the genuinely-live
`scopes_panel.py`/`stack_panel.py`. Re-checked this session against
`main_window.py`'s actual imports (not assumed) -- neither file is
imported anywhere; both are dead code, same "Legacy / orphaned" status
`KNOWN_GOOD_SUMMARY_2026-07-24-8.md` already listed them under. The hint-
text edits landed in both anyway this session (harmless, but pointless --
dead code doesn't affect the running app). Flagging so a future pass
doesn't treat their presence in RUNNING.md's file lists as proof they're
live.

## Architecture

Unchanged from #8 -- see that file (and #7/#6/#5 before it) for the full
rundown of `stack.py`, Stack Forking, per-operator backends, library
backends, `undo_history.py`, `sample_image_panel.py`, `session_io.py`,
`floating_dialog.py`, `workspace_io.py`, and the QMainWindow/QDockWidget
layout itself. This checkpoint's changes were entirely `style.py` (the
fixed palette + QSS) plus `wipe_compare_widget.py`'s background-fill
fix -- no structural/behavioral code touched.

## Test status

`python3 -m pytest -q` — **307 passed, 19 failed**, 25 `test_*.py`
files. All 19 failures are the same pre-existing `PermissionError` on
hardcoded `/tmp/test_*` paths (sandbox-specific, not a code issue) --
all in `test_format_writers.py`/`test_image_preview.py`. Re-confirmed
fresh after every color pass this checkpoint (#62 through #67); count
never moved, as expected for paint-only changes.

## Known limitations

- **Nothing in this checkpoint's own work confirmed on real hardware
  yet** -- every pass (#62-#67) was verified via headless `win.grab()` +
  pixel sampling, not an actual display. Tom's "This is what i was
  hoping for" (#67) is the strongest signal so far that the current
  values are right, but that was said in-chat, not confirmed as tested
  on the packaged `.app` or a fresh launch.
- **Panels are now darker than the space between them** (`#141414` vs.
  `#242427`) -- a deliberate inversion of this file's original "cards
  lighter than background" design language, per Tom's explicit asks
  through #67. Not a bug, just worth remembering if a future color pass
  starts from the OLD "panels should read lighter" assumption baked into
  `style.py`'s older comments.
- **The GL-safety gap on the drag-to-float path** (see RUNNING.md #40,
  item 6) -- carried over from #8, not touched this checkpoint.
- **App icon**: smallest-size legibility issue (flagged 07-23) — not
  touched since.
- **`setup.py`'s `includes` list still looks incomplete** — likely
  harmless, still unverified against a real build.
- **Retarget is still single-pair**.
- **Stack Forking: no within-branch or cross-branch drag reordering yet**.
- **A real on-screen QA pass of the fork table** — still only verified
  headlessly.
- **Graph panel's Y curve X-axis**: plotted against `input_r`
  specifically — still flagged, not solved.
- **Cage mesh edge count grows fast with Grid density** — unverified
  whether that's too slow in practice on real hardware.

## Backlog

- **Real-hardware confirmation of the #62-#67 color arc** -- the natural
  next step given nothing above has been checked outside this sandbox.
- Stack Forking: within-branch drag reordering (see Known limitations).
- App icon small-size legibility (see Known limitations).
- Retarget 4-field expansion (see Known limitations).
- A real on-screen QA pass of the fork table (see Known limitations).

## Files

**Active** (imported by `main_window.py`, directly or transitively — 35
modules, unchanged from #8's list; contents of `style.py` and
`wipe_compare_widget.py` changed this checkpoint, the rest did not):
`main_window.py`, `browser_panel.py`, `stack_memories_panel.py`,
`lut_library_panel.py`, `cdl_library_panel.py`, `image_library_panel.py`,
`sample_image_panel.py`, `stack_panel.py`, `scopes_panel.py`,
`panel_frame.py`, `style.py`, `stack.py`, `stack_memory.py`,
`lut_library.py`, `cdl_library.py`, `image_library.py`, `undo_history.py`,
`help_text.py`, `app_logic.py`, `session_io.py`, `clf_adapter.py`,
`format_writers.py`, `wipe_compare_widget.py`, `lut_viewer_3d.py`,
`scopes.py`, `colorspace.py`, `colorspace_convert.py`, `retarget.py`,
`printer_lights.py`, `aces_drt.py`, `cdl.py`, `cdl_file_io.py`,
`stack_memory_file_io.py`, `color_balance.py`, `color_wheel_widget.py`,
`floating_dialog.py`, `workspace_io.py`.

Modified this checkpoint: `style.py` (all six passes, #62-#67),
`wipe_compare_widget.py` (#64's background-fill fix), `scopes_panel.py`
and `stack_panel.py` (#66's hint-text literal-hex fix, both genuinely
live files).

**Legacy / orphaned** — unchanged from #8, still candidates for deletion
if the repo needs tidying: `concatenate_panel.py`, `aces_drt_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py`. **Note:** the latter
two received #66's hint-text literal-hex edit too this checkpoint
(harmless -- see "A note on..." above for why that happened and why it
doesn't matter for the running app).

**Harmless leftover artifacts** (sandbox couldn't delete them):
`main_window.py.bak`, `sample_image_panel.py.bak`, `scopes_panel.py.bak`,
`stack_panel.py.bak` from an old `sed -i.bak`, still present, still
harmless, still ignorable.

**Tests**: 25 `test_*.py` files, 307 passing (see Test status above).

**Docs**: `USER_MANUAL.md`/`help_text.py`, `BUILD_ON_MAC.md`, `RUNNING.md`
(up to date through #67), `KNOWN_GOOD_SUMMARY_2026-07-23.md` through
`KNOWN_GOOD_SUMMARY_2026-07-24-8.md` (previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`)
produces `dist/LUTier.app`, universal2. Icon via
`make_icon.sh AppIcon_source.png` (macOS-only). Worth a fresh build
before Tom's next real-hardware check, since nothing this checkpoint has
been verified against the packaged `.app` specifically.

**Environment (worth reading before the next session starts cold):**
`requirements.txt` covers PySide6, pyqtgraph, PyOpenGL, scipy,
colour-science, colour-clf-io, numpy, Pillow, opencolorio. The
previously-documented venv at `/tmp/lutier_venv` was NOT usable this
session -- its `site-packages` is owned by a different sandbox user
(`nobody`, not this session's user), so pip can't write into it, and
`shiboken6`/`pyqtgraph`/`PyOpenGL` were missing from it again besides.
Built a fresh venv at `/tmp/lutier_venv2` instead. One real gotcha hit
along the way: the default `TMPDIR` (`/sessions/<session>/tmp`) sits on
a filesystem that's essentially full (a few MB free) in this sandbox,
which made `pip install` fail with a misleading "No space left on
device" on anything bigger than ~3MB even though the actual venv target
disk (`/`) had 2+ GB free -- fixed by passing `TMPDIR=/tmp/piptmp2` and
`--cache-dir=/tmp/pipcache2` (both on the roomier `/` filesystem)
explicitly to pip. The `.eglstub/libEGL.so.1` workaround (in this
project's `outputs/.eglstub/`, per Tom's earlier sessions) is still
required and still works -- `LD_LIBRARY_PATH=.../outputs/.eglstub`
alongside `QT_QPA_PLATFORM=offscreen`. A future session should expect to
repeat this same venv-rebuild dance unless `/tmp/lutier_venv`'s
permissions happen to change.
