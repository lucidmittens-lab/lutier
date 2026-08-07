# LUTier — Dead Code & Performance Observations (2026-07-25)

Originally written as observations only. **Update, same day:** items
1-3 from "Suggested order if acting on this" below, plus #4's
`panel_frame.py` dead branch, have since been executed -- see
`RUNNING.md` #68 for the full account (what changed, how each was
verified). Left this file's original text below unmodified as the
record of what was found and why; #6/#7 (the `dist`/`build` bloat and
the `hdr_atom_tool.zip` duplicate) and #10/#11's "already fine, no
action needed" notes are still just observations, not yet acted on.

Compiled by tracing `main_window.py`'s actual import graph (not assumed
from docstrings/comments), reading the flagged files directly, and
reading the hot paths a live preview edit (dragging a stack-stage
slider, moving the wipe divider) actually runs through. Ordered roughly
by impact-for-effort.

## Dead code

### 1. Four Python files are fully unreachable from `main_window.py`

Traced the whole import graph starting at `main_window.py` (not just
grepped for the word "orphaned" in comments). These four are never
imported by anything reachable from the app's entry point:

- `aces_drt_panel.py` (218 lines)
- `concatenate_panel.py` (284 lines)
- `lut_controls_panel.py` (429 lines)
- `collapsible_section.py` (59 lines)

990 lines total. This matches what `KNOWN_GOOD_SUMMARY_2026-07-24-8.md`
already listed as "Legacy / orphaned" -- confirming that list is still
accurate, not stale. Safe to delete outright; nothing imports them.

**One catch:** `setup.py`'s py2app `includes` list (see #3 below) still
names `collapsible_section` -- delete that line too, or the build will
try to bundle a file that no longer exists.

### 2. Four stale `.bak` files, now actually deletable

`main_window.py.bak`, `sample_image_panel.py.bak`, `scopes_panel.py.bak`,
`stack_panel.py.bak` -- leftovers from an old `sed -i.bak` invocation,
previously flagged across several `KNOWN_GOOD_SUMMARY` checkpoints as
"harmless, sandbox couldn't delete them." Checked ownership this
session: all four are owned by the current session user (not `nobody`,
unlike the stuck venv files) -- they're actually removable now, whatever
permission issue blocked earlier sessions isn't present here. 7,252
lines / ~322 KB combined, doing nothing but clutter.

### 3. `setup.py`'s py2app `includes` list is wrong in both directions

`KNOWN_GOOD_SUMMARY_2026-07-24-8.md` already flagged this list as
"looks incomplete, unverified against a real build" -- checked it
concretely this session by diffing it against the real import graph:

- **Includes one dead module:** `'collapsible_section'` (see #1 above)
  -- ships as dead weight in the packaged `.app` if left in.
- **Missing thirteen live local modules** that `main_window.py` actually
  needs transitively: `cdl`, `cdl_file_io`, `cdl_library`,
  `cdl_library_panel`, `color_balance`, `color_wheel_widget`,
  `colorspace_convert`, `floating_dialog`, `lut_library`,
  `lut_library_panel`, `stack_memory_file_io`, `undo_history`,
  `workspace_io`.

py2app's own automatic dependency scan (modulegraph) usually catches
plain `import` statements without needing `includes` at all, which is
probably why the packaged `.app` has reportedly still worked -- but
`includes` exists specifically as the failsafe for whatever that scan
misses, and right now it can't be trusted to catch anything, since it's
missing over a third of this app's own local modules. Worth an explicit
test build to confirm nothing's actually silently relying on it, then
fixing the list either way so it reflects the real dependency set.

### 4. `panel_frame.py`: an entire code path is unreachable in a LIVE file

Different from #1-3 -- this is dead code living INSIDE a file that's
otherwise very much in use. `PanelFrame.__init__(self, title,
content_widget, corner_widget=None, parent=None)` has an `if title:`
branch that builds `title_row_widget`, the title `QLabel`, and places
`corner_widget` on that row. Checked every call site in the app: there
is exactly one, `PanelFrame(None, content_widget)` in `main_window.py`
-- `title` is always `None`, `corner_widget` is never passed. That means
the entire `if title:` block (~24 lines) is unreachable in the running
app; every real panel's title bar is actually built by
`main_window._build_dock_title_bar` instead (a parallel, separate
implementation that duplicates the `#panelTitleRow`/`#panelTitle` object
names on purpose, per that function's own docstring, so `style.py`
doesn't need two sets of rules).

This one's already self-documented in the file's own docstring ("title
may be None/'' to render NO title bar... Left in since it's a genuinely
useful capability... may well get used again") -- flagging it here
mainly so it's in one consolidated list rather than only living as a
comment. Whether to actually delete it is a judgment call (the docstring
argues for keeping it as a deliberately-preserved capability), not a
clear-cut dead-code removal like #1-2.

### 5. An empty directory: `gui/`

Zero files inside. Harmless, but it's not obvious from the directory
listing alone whether it's an intentional placeholder or a leftover from
an abandoned reorganization. Worth a one-line answer either way.

## Repo/disk bloat (not code, but directly caused friction this session)

### 6. `dist/` (1.6 GB) and `build/` (35 MB) are persisted in the working tree

This is very likely why installing dependencies at the start of this
session hit real "No space left on device" errors (traced to the
sandbox's `/tmp`-adjacent filesystem being nearly full) -- `dist/` alone
is over 80% of this project's entire 2.0 GB working-directory footprint.
These are py2app BUILD OUTPUTS (a full bundled Python + Qt frameworks +
every dependency, per `.app`), not source -- there's no reason for them
to persist and accumulate across sessions the way `RUNNING.md`/the
actual source files need to. Worth treating `dist/`/`build/` as
disposable (rebuild fresh when actually needed, don't carry them forward
session to session) rather than letting them sit and grow.

### 7. `hdr_atom_tool.zip` (32 MB) duplicates the already-extracted `hdr_atom_tool/` (71 MB)

Lower confidence/priority than the rest of this list since it's a
different tool entirely, not part of LUTier -- flagging only because
it's sitting in the same working directory and the zip looks like a
redundant copy of the folder right next to it. Worth confirming whether
the zip is still needed before removing it.

## Performance

### 8. Stack evaluation (`stack.py`) -- already well-optimized, no action needed

Worth stating explicitly rather than leaving it unmentioned: `StageCache`
already does block-level incremental memoization (nudging one stage's
opacity slider only re-runs that one block, not the whole stack), and
its own docstring documents exactly the sluggishness it was built to
fix. Read through `bake_stack_to_lut3d`/`apply_stack_to_image_array`/
`StageCache.run` looking for redundant work and didn't find any --
this is in good shape.

### 9. Every live preview repaint round-trips through PNG encode + decode

**The one concrete, actionable performance finding.** 
`SampleImagePanel._array_to_pixmap` (`sample_image_panel.py`) is the
only path that turns a numpy array into something `WipeCompareWidget`
can draw:

```python
@staticmethod
def _array_to_pixmap(image_array):
    png_bytes = array_to_png_bytes(image_array)
    pixmap = QPixmap()
    pixmap.loadFromData(png_bytes, "PNG")
    return pixmap
```

`array_to_png_bytes` (`app_logic.py`) does a real PIL PNG encode
(zlib compression) into an in-memory buffer, which then gets handed
straight back to Qt to PNG-DECODE via `loadFromData`. This runs on
`_render_wipe`, which `refresh_preview`'s own docstring says fires "on
every stack mutation" -- meaning every live slider drag on a stage's
opacity, every printer-lights trim tweak, every CDL adjustment pays for
a full PNG compress-then-decompress cycle, purely to move pixels that
are already sitting in memory as a numpy array into a `QPixmap`. PNG
compression is real, nontrivial CPU work (far more than a raw copy) on
exactly the path where latency matters most -- the "does this feel
responsive while I'm dragging a slider" path.

The fix doesn't need `array_to_png_bytes` touched at all (it's still the
right tool for `export_image_array_to_file`'s file-writing use, and for
any actual "give me PNG bytes" caller) -- just a second, PNG-free path
for the in-memory preview case: convert the float array to a `uint8`
buffer once, then build a `QImage` directly from that buffer (Qt can
wrap a raw `RGB888`-format buffer with no compression step at all) and
`QPixmap.fromImage(...)` it, instead of going through
`array_to_png_bytes` + `loadFromData`. Same output, no compression
round-trip.

### 10. `lut_viewer_3d.py`'s density/opacity handling -- already reasonably optimized

`_on_density_changed` and `rebuild_gl_view` both have explicit comments
about NOT redoing the 1D curve plot when only the 3D scatter/cage needs
updating, and about not re-running 3D-view work redundantly across a
float/dock cycle. Read through looking for a similar "recomputes more
than it needs to" pattern as #9 and didn't find one here.

### 11. `scopes_panel.py`'s scope switching -- already fine

`_render_current_scope` dispatches on whichever ONE of
Histogram/Waveform/Vectorscope is actually selected -- it doesn't
compute all three and just hide two. No action needed.

## Suggested order if acting on this

1. Delete the four dead files (#1) and the `.bak` files (#2) -- zero
   risk, pure cleanup.
2. Fix `setup.py`'s `includes` list (#3) -- remove the dead entry, add
   the thirteen missing live modules, then do one real test build to
   confirm nothing was silently depending on the current wrong list.
3. Fix `_array_to_pixmap`'s PNG round-trip (#9) -- the one change here
   that should produce a noticeable, measurable difference (live-preview
   responsiveness while dragging any stack control).
4. `dist/`/`build/` (#6) -- not code, but worth a decision on whether to
   keep clearing these between sessions given they're most of this
   project's disk footprint.
5. Everything else (`panel_frame.py`'s dead branch, the empty `gui/`
   dir, `hdr_atom_tool.zip`) is low-stakes housekeeping, worth doing but
   not urgent.
