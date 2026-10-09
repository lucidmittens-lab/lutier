"""sample_image_panel/constants.py -- Qt-free settings for the Record viewer:
edit and scrub downres factors and settle debounces, the Scopes sample size
cap, wipe source choices, the matte-highlight overlay, mode labels and
transport glyphs.
"""
import numpy as np


# PREVIEW RESOLUTION (2026-08-09 rework, see RUNNING.md and
# docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md's "band-aid" note) -- Sample/
# Reference now always load at NATIVE resolution (max_dimension=None to
# load_image_as_array), no capped preview size and no reactive zoom/
# resize-triggered reload. WipeCompareWidget's own _image_rect() already
# scales whatever pixmap it's handed to fit the panel at the current zoom
# (fit_scale * zoom) -- that scaling is all panel-size/zoom ever needed to
# be handled by; re-decoding the SOURCE at a bigger size in response to
# zooming in was solving the same problem a second, redundant way, and
# per Tom, it read as an obvious performance band-aid (a visible reload
# pop when zooming) standing in for a problem it wasn't actually the
# right tool for. What actually costs real time at native resolution is
# apply_stack_to_image_array itself during a live edit -- see
# EDIT_DOWNRES_FACTOR/refresh_preview below, which is the real fix for
# that: temporary reduced-resolution STACK APPLY during a rapid burst of
# edits, not a reduced-resolution SOURCE decode tied to zoom.
EDIT_DOWNRES_FACTOR = 2
# Long-edge cap (pixels) for the array a GPU-eligible stack's CPU
# recompute uses -- see refresh_preview's own comment, 2026-08-10
# follow-up: for a GPU-eligible stack, this CPU recompute exists ONLY
# for ScopesPanel (the Viewer itself renders on the GPU directly, at
# native resolution, regardless of this array -- see
# _array_and_gpu_stack_for_source), so there's no reason it should ever
# touch native resolution at all, no matter how expensive the CPU-side
# math for some stage in the stack happens to be. Confirmed directly why
# this matters, not assumed: colour.LUT3D.apply() (a 'lut' stage's CPU
# path) measured 0.5-3.3 SECONDS at native/half preview resolutions
# (1920x1080 to 3840x2160) -- multiple SECONDS of UI-thread stall on
# every single edit tick for a stack containing a cube, even though the
# Viewer itself was already fast via GPU Stage 4. Scopes (histogram/
# waveform/vectorscope) are inherently statistical displays -- a few
# hundred thousand representative pixels is plenty, matching how a
# strided decimation already works for EDIT_DOWNRES_FACTOR above, just a
# fixed target SIZE (robust across wildly different source resolutions)
# rather than a fixed divisor.
SCOPES_MAX_DIMENSION = 512
# How long a stream of Stack edits has to go quiet before the preview
# snaps back to a full-resolution render -- short enough to feel
# immediate once you stop dragging, same spirit as the debounce already
# proven out for undo-history coalescing (main_window._UNDO_DEBOUNCE_MS)
# and the old resize-reload timer this replaces, just its own timer/value
# since "when to stop showing a reduced-quality preview" and "when to
# commit an undo step" are related but genuinely separate questions.
EDIT_SETTLE_DEBOUNCE_MS = 150

# Scrub-time downres (per Tom: "make scrub drop the render resolution to
# half until settled so its more performant") -- same settle-timer/burst-
# detection SHAPE as EDIT_SETTLE_DEBOUNCE_MS/_editing_settle_timer above,
# applied to the transport scrub bar instead of Stack edits. The scrub
# bar's own valueChanged fires continuously while dragging (QSlider's
# default tracking=True), so an ISOLATED single valueChanged (a plain
# click-to-jump) still decodes at full/normal resolution immediately --
# only a genuine BURST of rapid calls (an actual drag) gets temporarily
# downres'd, with one final full-quality decode once the burst goes
# quiet. See _on_video_scrub_slider_changed/_on_scrub_settled. Applies to
# BOTH scrub modes ("globally"): SOURCE (seek_video_frame, which decodes
# at full native resolution when settled -- currently NO cap at all, the
# main cost this targets) and RECORD (TimelineController.
# seek_to_record_frame, which already always caps to DECODE_PROXY_MAX_
# DIMENSION even when settled -- see that module's own docstring on why
# Timeline clips have no full-native "parked" mode; this only adds a
# temporary EXTRA halving of that existing cap during a burst).
SCRUB_DOWNRES_FACTOR = 2
SCRUB_SETTLE_DEBOUNCE_MS = 150

# The three things either side of the wipe (or the single view, when the
# wipe's disabled -- see _render_wipe, which reuses the Right selection as
# the single-view source rather than tracking a fourth independent state)
# can show -- see this module's docstring on Source vs Reference. "Source
# Before"/"Source After" are the pre-/post-stack states of the one Source
# image this panel always had; "Reference" is the new second image the
# Stack never touches. Left/Right are picked from "Left"/"Right" tree
# submenus in the "..." menu (per Tom -- see populate_menu/
# _build_wipe_source_submenu), each built as one checkable QAction per
# entry in this list, so the actual dispatch logic (_pixmap_for_source)
# only ever has to compare against the stable key, never the display
# label.
WIPE_SOURCE_SAMPLE_BEFORE = "sample_before"
WIPE_SOURCE_SAMPLE_AFTER = "sample_after"
WIPE_SOURCE_REFERENCE = "reference"
WIPE_SOURCE_CHOICES = [
    ("Source Before", WIPE_SOURCE_SAMPLE_BEFORE),
    ("Source After", WIPE_SOURCE_SAMPLE_AFTER),
    ("Reference", WIPE_SOURCE_REFERENCE),
]


# Flat neutral level painted outside the key in a Highlight-mode preview
# (see _compose_highlight_overlay) -- per Tom: "solid gray for pixels
# outside of the key," replacing an earlier per-pixel luma-grayscale
# version (which still showed the ORIGINAL image's own brightness detail
# outside the key, competing with the colored key itself for attention
# instead of receding behind it, same as Resolve's own flat-gray
# Highlight background does).
HIGHLIGHT_GRAY_LEVEL = 0.2


def _compose_highlight_overlay(graded_array, matte):
    """MATTE HIGHLIGHT (per Tom: "a way to preview the mask similar to
    resolve's highlight mode") -- `graded_array` (H,W,3) where `matte`
    (H,W, same convention every matte-producing stage kind already
    returns -- see stack.py's docstring, "MATTE CHANNELS") is high, a
    FLAT HIGHLIGHT_GRAY_LEVEL gray (not the original image's own
    brightness) where it's low, smoothly blended in between by the
    matte's own soft edge. Pure numpy, no Qt -- called from
    _array_and_gpu_stack_for_source's own "MATTE HIGHLIGHT" branch, kept
    as its own free function so it's directly unit-testable without
    constructing a real SampleImagePanel."""
    gray = np.full_like(graded_array, HIGHLIGHT_GRAY_LEVEL)
    m = matte[..., np.newaxis]
    return graded_array * m + gray * (1.0 - m)


# "❚❚", not "⏸": DejaVu Sans (and the other common UI fonts) have U+275A
# but not U+23F8, which falls back to a math/emoji font with a different
# line height -- see _fix_size_across_texts.
# The Record viewer's two modes, as shown on its badge (internal names stay
# "source"/"record").
MODE_LABELS = {"source": "CLIP", "record": "TIMELINE"}

_PLAY_GLYPH = "▶"
_PAUSE_GLYPH = "❚❚"
