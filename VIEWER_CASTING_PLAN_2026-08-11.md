# Viewer casting (AirPlay now, BMD DeckLink/UltraStudio later) -- 2026-08-11

## Why

Tom wants to push the Viewer's live graded output to an external display.
First target: AirPlay, 10-bit HEVC 4:2:0 acceptable (he confirmed dropping
4:2:2 -- AirPlay's actual hardware/software stack, Apple TV included, is
built around 4:2:0 chroma; 4:2:2 was never realistically on the table for
a consumer AirPlay pipeline). He also wants this built with an eye toward
a LATER follow-up: pushing video via a Blackmagic UltraStudio/DeckLink
SDI card -- not built in this pass, but the architecture below is shaped
so it can slot in without touching the interface or the calling code.

Built autonomously (Tom stepped away) -- every step below was verified
against the REAL installed frameworks (PyObjC + AVFoundation/AVKit/
CoreMedia/Quartz) in this environment, not assumed from documentation
alone, same "measure the real thing" discipline this whole project holds
for its GPU work. The one thing that genuinely CANNOT be verified without
Tom present is real wireless casting to a physical AirPlay receiver --
see "What's NOT verified" below.

## A real finding that reshaped the scope

The original framing (from the conversation that led here) was "an
in-app Cast button with an AirPlay device picker." That turned out not
to be buildable as described, and rather than write speculative code
against a guessed API, the actual Apple frameworks were checked directly
first:

- `AVRoutePickerView` (AVKit) is real and constructs fine on macOS, but
  its ONLY functional hook is `setPlayer:`, which strictly wants a real
  `AVPlayer`. Confirmed by listing its actual Objective-C methods.
- `AVSampleBufferDisplayLayer` (the correct, Apple-documented layer for
  enqueuing custom `CMSampleBuffer`s from a non-file frame source, per
  Apple's own "Integrating AirPlay for long-form video apps" doc) has
  **no** routing-related API of its own at all -- checked directly (no
  "route"/"airplay"/"external" members).
- Conclusion, confirmed rather than assumed: there is no publicly
  documented way for a third-party app to drive AirPlay DESTINATION
  SELECTION for a custom (non-`AVPlayer`) frame source.

**What this sink actually does instead:** renders the graded frame into a
dedicated, chrome-free native `NSWindow` (title "LUTier — Independent
Viewer") via a real `AVSampleBufferDisplayLayer`. This is honest, real,
and buildable; a true in-app route picker for custom content is not, as
of this research pass.

**SECOND real finding, from Tom's own live testing (2026-08-11):** the
plan above originally claimed "Control Center -> Screen Mirroring lists
individual windows on modern macOS" -- Tom tested this directly and it's
wrong. Screen Mirroring offers exactly two choices: "Mirror" (the whole
desktop) or "Use As Separate Display" (a genuine new `NSScreen`,
extending the desktop onto the AirPlay receiver) -- no per-window picker
exists. "Use As Separate Display" is the one that gets a clean, isolated
feed: drag the Independent Viewer window onto that new screen and
fullscreen it there, and the receiver shows ONLY that window. Plain
Mirror shows the receiver the whole desktop. The window was renamed from
"LUTier Cast" to "LUTier — Independent Viewer" (and the menu action from
"Cast to AirPlay Window" to "Open Independent Viewer Window") to stop
implying an AirPlay-specific capability that doesn't exist -- what the
window is actually used for (AirPlay via Separate Display, a plain
second monitor, anything else) is entirely up to the user now, and the
naming reflects that. Automating the drag/fullscreen-onto-the-AirPlay-
screen step was offered as a follow-up; Tom declined it for now.

## Architecture (REVISED 2026-08-12 -- "fork 3 ways from one shared source")

Tom's own explicit request, after confirming the first version worked but
was noticeably slow to update: don't re-derive the graded picture
independently for each consumer -- fork it. The Stack's processed picture
now forks THREE ways from one shared source: the Viewer panel (always),
the Independent Viewer window (if enabled), and a future DeckLink/
UltraStudio hardware output (if enabled). All three show the exact same
pixels; none of them recompute it.

```
wipe_compare_widget.WipeCompareWidget
  - paints the Viewer panel as it always has (UNCHANGED -- see below)
  - read_back_after_frame(): ON-DEMAND readback of whatever the 'after'
    side is CURRENTLY showing -- the exact pixels the Viewer panel
    itself just painted. Correct for BOTH the GPU-eligible case (reads
    the cached graded GPU texture) and the GPU-ineligible case (that
    texture already IS the CPU-graded array, uploaded verbatim) with no
    separate branch. Does NOT touch paintGL's own code path -- it's a
    wholly separate method, makeCurrent()/doneCurrent()-bracketed,
    costing the interactive-editing hot path nothing when nothing polls it
        |
        v  (the ONE shared frame source)
sample_image_panel.py ("Open Independent Viewer Window" menu action)
        |
        v
casting_controller.CastingController
  - frame_getter = wipe_compare_widget.read_back_after_frame
  - `_sinks`: a LIST (not one slot) -- the Independent Viewer and a
    future DeckLink sink can both be active at once, each fed identical
    frames
  - still debounced (250ms) -- not to protect against a slow CPU
    recompute anymore, just to avoid running the whole YCbCr-convert/
    CVPixelBuffer/CMSampleBuffer pipeline (real, non-zero per-consumer
    work) on every single slider-drag tick
        |
        v
video_output_sink.VideoOutputSink (abstract)
  - VideoFrame: plain (H, W, 3) float32 RGB, no destination-specific
    pixel format baked in
        |
        v
airplay_output_sink.AirPlayOutputSink  <-- built now
  - ycbcr_convert.py: RGB -> BT.709 10-bit 4:2:0 biplanar (pure numpy,
    zero hardware dependency, fully unit tested)
  - CVPixelBuffer -> CMSampleBuffer -> AVSampleBufferDisplayLayer,
    hosted in a dedicated NSWindow

decklink_output_sink.DeckLinkOutputSink  <-- NOT built, future follow-up
  - same VideoOutputSink interface; would convert VideoFrame's RGB into
    whatever pixel format the Blackmagic DeckLink SDK's frame-output API
    wants (typically 10-bit YUV/v210 over SDI) and hand frames to that
    SDK instead -- casting_controller.py and sample_image_panel.py would
    need ZERO changes to support this beyond a new sink class and a menu
    entry that constructs it (start_sink/stop_sink already support
    multiple simultaneously-active sinks).
```

`VideoOutputSink`/`VideoFrame` (video_output_sink.py) are the ONLY shared
surface between the two backends, deliberately kept free of both
AirPlay-specific (CVPixelBuffer/CMSampleBuffer) and DeckLink-specific
(Blackmagic's own C++ SDK) vocabulary -- same "Qt-free math + thin
backend wrapper" split this app already uses for cdl.py/aces_drt.py/etc.,
generalized to "sink-free frame producer + thin per-destination sink."

### Why this doesn't touch `paintGL`'s own code path

The first version of this feature (below, superseded) deliberately ran
its OWN independent CPU recompute specifically to avoid adding readback
plumbing to `WipeCompareWidget.paintGL` -- the single most hardened,
crash-prone hot path in this codebase (see that file's own docstring on
the real, reproduced Shiboken/SIGSEGV crash fixed there earlier this
session). Tom explicitly asked for the shared-source design instead. The
actual implementation still respects that constraint:
`read_back_after_frame` is a wholly SEPARATE, on-demand method --
`paintGL`/`_paint_gl_impl` were not modified AT ALL. It reads whatever
texture the LAST real paint already left cached in `_SideState`
(`_after_state.graded_texture`), using `makeCurrent()`/`doneCurrent()` to
get a valid GL context outside the normal paint cycle (a widget's GL
context is only automatically current during Qt's own `paintGL`
callback). `CastingController` only calls it from its own debounce timer,
never from inside a paint callback -- so this feature costs the
interactive-editing hot path exactly nothing when no sink is active.

**A real bug found and fixed while building this:** `_SideState`'s
cached GL texture handles go stale once `_paint_gl_impl` stops calling
`ensure_texture` at all -- which happens whenever `_image_rect()` returns
`None` (nothing to draw, e.g. right after `clear()`), since that's an
early return before `ensure_texture` (the only place that clears
`graded_texture`) ever runs. `read_back_after_frame` would otherwise hand
back a STALE, deleted-in-spirit-but-still-referenced texture forever
after a `clear()`. Fixed by checking `_after_state.array` (updated
immediately by `clear()`, no paintGL round trip needed) instead of
trusting the possibly-stale cached texture handle alone -- found by
`test_wipe_compare_widget_readback.py`'s own
`test_read_back_after_frame_returns_none_after_clear`, not by inspection.

### Superseded: independent CPU recompute (2026-08-11, first version)

The original design ran `stack.apply_stack_to_image_array` itself,
independently of the live GPU preview, specifically to avoid touching
`paintGL`. Real and correct, but Tom confirmed by testing it that this
was noticeably slow to update -- a `lut` stage can cost 0.5-3.3 seconds
per CPU recompute (see `GPU_ACCELERATION_PLAN_2026-08-08.md`), and even
without one, a full native-resolution CPU recompute on every debounce
tick is real, avoidable cost the new shared-GPU-texture-readback design
doesn't pay at all.

### Debounce -> throttle fix (2026-08-12)

The first version of `CastingController.on_stack_or_image_changed()`
used a pure debounce: a `QTimer(singleShot=True)` restarted via
`.start()` on every call. Tom tried the shared-source rework above and
confirmed it was "way faster to update but seems to only happen once i
stop changing a parameter." Root cause: restarting an already-running
singleShot timer resets its countdown (standard Qt behavior), so a
continuous burst of calls (every tick of a slider drag fires
`refresh_preview()` -> `on_stack_or_image_changed()`) kept postponing the
fire indefinitely -- it only ever ran once ticks stopped arriving for a
full `_THROTTLE_SECONDS`. That's correct debounce behavior, just the
wrong pattern for a live cast window: with the old slow CPU-recompute
path, avoiding any push during a drag was the right call, but now that a
push is cheap (a GPU texture readback, not a recompute), the window
should update periodically *during* the drag too.

Fixed by switching to a leading+trailing throttle (`_THROTTLE_SECONDS =
0.25`, its own constant, renamed from `_DEBOUNCE_MS`): if at least
`_THROTTLE_SECONDS` has passed since the last real push,
`on_stack_or_image_changed()` pushes immediately (leading edge -- this is
what makes a drag update roughly 4x/sec instead of once at the end);
otherwise it schedules exactly one trailing push for whenever the window
closes, so the last edit in a burst is never dropped (the same guarantee
the old debounce gave, just no longer the *only* time it fires).

## What's verified (real, in this environment)

- `ycbcr_convert.py`'s BT.709 matrix math, 10-bit video-range
  quantization, chroma box-downsampling, and 10-bit-in-16-bit packing --
  10 unit tests, pure numpy, zero hardware dependency
  (`test_ycbcr_convert.py`).
- `CVPixelBufferCreate(..., kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange, ...)`
  succeeds and reports 2 planes at the correct 4:2:0 dimensions; its own
  `PixelFormatDescription` metadata (`BitsPerComponent=10`, plane 0
  `BitsPerBlock=16`, plane 1 `BitsPerBlock=32` with 2x/2x subsampling)
  independently CONFIRMS `ycbcr_convert.py`'s packing/layout assumptions
  match the pixel format's real declared layout -- this wasn't assumed,
  the framework told us its own layout and it matched.
- Writing real numpy plane data into a locked `CVPixelBuffer`'s memory
  (`CVPixelBufferGetBaseAddressOfPlane(...).as_buffer(...)`, a writable
  memoryview) and reading it back confirms real, correct memory writes,
  respecting each plane's own `bytesPerRow` (which exceeds the tightly-
  packed row width -- IOSurface-backed buffers pad for GPU alignment).
- `CMVideoFormatDescriptionCreateForImageBuffer` ->
  `CMSampleBufferCreateForImageBuffer` -> `AVSampleBufferDisplayLayer.
  enqueueSampleBuffer_` all succeed (status 0), and the layer reports
  `status()==1` (rendering) with `error()==None` after enqueuing.
- `NSWindow` + `NSView(wantsLayer)` + `AVSampleBufferDisplayLayer` as a
  sublayer constructs and shows without error, riding on the same Cocoa
  run loop PySide6/Qt already runs on macOS (Qt IS a Cocoa app under the
  hood -- no separate `NSApplication` run loop needed).
- `WipeCompareWidget.read_back_after_frame` -- 5 tests against a REAL,
  shown (not offscreen-QPA) widget: returns `None` before any
  `set_after`, matches CPU grading within GPU tolerance for a GPU-
  eligible stack (max|delta| ~2e-7 once both sides are clipped to
  [0,1] -- `render_chain` itself is deliberately unclamped, an HDR
  result can legitimately exceed it), reflects a live stack edit on the
  very next readback, hands back an already-CPU-graded array verbatim
  for a GPU-ineligible stack, and returns `None` again after `clear()`
  (the stale-texture bug above, confirmed fixed)
  (`test_wipe_compare_widget_readback.py`).
- The full pipeline end to end through the REAL app: `MainWindow` with a
  loaded sample image and a CDL stage, toggling "Open Independent Viewer
  Window" on -> real window opens, a real graded frame -- READ BACK FROM
  THE VIEWER'S OWN RENDERED TEXTURE, not independently recomputed --
  renders through the whole CVPixelBuffer/CMSampleBuffer/
  AVSampleBufferDisplayLayer chain with no error; editing the live Stack
  afterward (`slope=(1.1,0.95,1.0)` -> `(1.4,0.6,1.0)`) produces a
  genuinely different pushed frame within one debounce cycle; toggling
  off cleanly closes the window. `MainWindow.closeEvent` also stops every
  active sink before the app quits (no stray window left behind).
- `CastingController`'s own orchestration against the new shared-source,
  multi-sink design -- idempotent `start_sink`/`stop_sink`/`stop_all`,
  starting a SECOND sink doesn't re-push a redundant frame to an
  already-active first one (a real bug found by this suite, then fixed),
  stopping one sink leaves another active sink untouched (directly
  covers the "fork 3 ways" design), the throttle pushes immediately once
  outside its window (leading edge) and schedules exactly one trailing
  push when a change lands inside it, a continuous ~20ms-tick burst over
  3.5 throttle windows produces multiple pushes DURING the burst (not
  just one at the end -- the direct regression test for the 2026-08-12
  debounce-vs-throttle fix above) with the final trailing push reflecting
  the very last edit, and `frame_getter` returning `None` means nothing
  is pushed anywhere -- 10 tests against fake `VideoOutputSink`s
  (`test_casting_controller.py`).
- `AirPlayOutputSink`'s own lifecycle (start/push/stop, idempotency,
  a size change mid-cast) against the REAL PyObjC/AVFoundation stack --
  8 tests (`test_airplay_output_sink.py`, unaffected by the shared-source
  rework -- this module's own contract didn't change, only who calls
  `push_frame` and with what did).

Full regression suite (`pytest -q`) clean except the one pre-existing,
unrelated `test_gl_reparent_safety.py` flake this session already knew
about before this feature started.

## What's NOT verified (needs Tom, in front of a real AirPlay receiver)

- **Actual wireless casting to a real AirPlay receiver.** No receiver
  hardware is available in this environment. Everything up to "a real,
  correctly-formatted 10-bit 4:2:0 frame renders in a real window with no
  error" is verified; whether dragging the window onto a "Use As Separate
  Display" AirPlay screen and fullscreening it there actually looks right
  on a real receiver (correct colors, no banding, acceptable latency)
  needs a real Mac + a real AirPlay receiver on the same network -- Tom
  confirmed the window itself opens and updates (if a bit slowly, being
  investigated) but hasn't yet confirmed the Separate-Display casting
  path end to end.
- **HDR/PQ signaling.** `ycbcr_convert.py` deliberately only does the SDR
  BT.709 path (see that module's own docstring) -- an ACES DRT HDR Viewer
  output would still cast through this same path (correctly
  range-mapped into legal 10-bit code values, just not flagged as HDR
  content to the receiver). Real HDR-over-AirPlay (BT.2020/PQ matrix,
  mastering-display/content-light-level metadata) is real additional
  scope, not attempted here.
- **Whether the exact bit-packing convention
  (`value16 = value10 << 6`) matches what a real receiver decodes.**
  This is a well-established cross-vendor convention (v210/P010/Apple's
  own biplanar 10-bit formats all use it), and `CVPixelBufferCreate`'s
  own reported plane metadata is consistent with it -- but that confirms
  internal self-consistency with Apple's OWN stated pixel format, not
  that a receiver decodes it identically (though if it didn't, that would
  be Apple's own pixel format contract being violated, not this module's
  bug).
- **`NSWindow` lifetime/threading across a full app session.** Verified
  fresh start/stop cycles and a session ending in `closeEvent`; not
  verified across, e.g., macOS display sleep/wake, external-monitor
  hot-plug, or a very long-running cast session.

## Follow-up: BMD UltraStudio/DeckLink SDI output (not built)

When Tom's ready for this: add `decklink_output_sink.py` implementing
`video_output_sink.VideoOutputSink` (same `start`/`push_frame`/`stop`/
`is_available`/`name` shape), using Blackmagic's own DeckLink SDK (a C++
SDK -- would need a Python binding, e.g. via `ctypes`/a small compiled
extension, or an existing third-party Python DeckLink wrapper if one's
vetted) to hand frames to the card's own frame-output API instead of a
CVPixelBuffer/AVSampleBufferDisplayLayer. `casting_controller.py` and the
Viewer's menu wiring need no changes beyond a new menu entry constructing
`DeckLinkOutputSink()` instead of `AirPlayOutputSink()` -- the whole
point of keeping `VideoOutputSink`/`VideoFrame` backend-agnostic.

## New dependencies

`pip3 install pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-AVFoundation
pyobjc-framework-AVKit pyobjc-framework-CoreMedia pyobjc-framework-Quartz`
(CoreVideo/`CVPixelBuffer*` has no standalone PyOpenColorIO-style package --
it lives inside the `Quartz` PyObjC bundle, confirmed directly). Added to
`setup.py`'s `OPTIONS['packages']` (compiled extensions, same reasoning as
`PyOpenColorIO`) so a future frozen build actually bundles this feature --
**a fresh `python3 setup.py py2app` build has NOT been run since this was
added**, only confirmed from source (`python3 splash_launcher.py`); do one
before shipping to confirm the frozen app bundles PyObjC correctly.
