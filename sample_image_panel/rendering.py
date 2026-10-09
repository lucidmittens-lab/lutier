"""sample_image_panel/rendering.py -- rendering the Record viewer: working,
downres and Scopes arrays, refresh_preview and the edit-settle recompute,
the matte highlight, the GPU stack for the Viewer, the wipe, casting,
full-resolution renders and still export. A mixin: SampleImagePanel
inherits it, so these methods share the panel's own `self`.
"""
import math
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QFileDialog, QMessageBox
from app_logic import load_image_as_array, export_image_array_to_file
from stack import apply_stack_to_image_array, running_array_before_stage
import gpu_stack_renderer
from airplay_output_sink import AirPlayOutputSink
from .constants import (
    EDIT_DOWNRES_FACTOR, EDIT_SETTLE_DEBOUNCE_MS, SCOPES_MAX_DIMENSION,
    WIPE_SOURCE_REFERENCE, WIPE_SOURCE_SAMPLE_AFTER,
    WIPE_SOURCE_SAMPLE_BEFORE, _compose_highlight_overlay,
)


class RenderMixin:
    def _recompute_working_array(self):
        """`_image_array` is always DERIVED from `_original_array`, never
        mutated in place -- was also where Adapt's colorspace conversion
        used to be applied before that feature was removed (per Tom:
        "remove the ability to adapt reference or source"); now a
        straight passthrough, kept as its own method rather than inlined
        at every call site so there's still one obvious place to look."""
        if self._original_array is None:
            self._image_array = None
            self._image_array_downres = None
            self._image_array_scopes = None
            return
        self._image_array = self._original_array
        self._image_array_downres = self._make_downres_array(self._image_array)
        self._image_array_scopes = self._make_scopes_array(self._image_array)
        self._image_array_generation += 1

    @staticmethod
    def _make_downres_array(array):
        """A cheap, strided decimation of `array` by EDIT_DOWNRES_FACTOR
        in each dimension -- deliberately nearest-neighbor (no
        interpolation cost at all), not a proper area-averaged resample:
        this is a temporary, throwaway, intentionally-lower-quality frame
        shown only while a burst of edits is actively in progress (see
        refresh_preview), not something anyone should be scrutinizing for
        quality -- spending real CPU time making the THROWAWAY frame look
        nicer would undercut the entire reason it exists. Floored at 1px
        per side so a very small source/panel can never decimate to a
        degenerate empty array."""
        factor = EDIT_DOWNRES_FACTOR
        decimated = array[::factor, ::factor]
        if decimated.shape[0] < 1 or decimated.shape[1] < 1:  # pragma: no cover -- only a 0px source could hit this
            return array
        return np.ascontiguousarray(decimated)

    @staticmethod
    def _make_scopes_array(array):
        """Same cheap strided-decimation approach as _make_downres_array,
        but targets a fixed long-edge SIZE (SCOPES_MAX_DIMENSION) rather
        than a fixed divisor -- robust across wildly different source
        resolutions (a 1080p source and an 8K source both end up around
        the same pixel count), which a fixed EDIT_DOWNRES_FACTOR-style
        divisor wouldn't guarantee. See SCOPES_MAX_DIMENSION's own
        comment for why this exists as a THIRD tier, separate from
        _image_array_downres."""
        h, w = array.shape[:2]
        long_edge = max(h, w)
        # ceil, not round -- round() can land on a factor that still
        # leaves the decimated long edge ABOVE SCOPES_MAX_DIMENSION
        # whenever the division's fractional part is under 0.5 (e.g.
        # 2200/512 = 4.30 rounds to 4, but a stride of 4 only gets a
        # 2200px edge down to 550px, still over the cap) -- ceil always
        # rounds the stride UP, guaranteeing the result stays at or
        # under the target.
        factor = max(1, math.ceil(long_edge / SCOPES_MAX_DIMENSION))
        decimated = array[::factor, ::factor]
        if decimated.shape[0] < 1 or decimated.shape[1] < 1:  # pragma: no cover -- only a 0px source could hit this
            return array
        return np.ascontiguousarray(decimated)

    def _recompute_reference_working_array(self):
        """Straight passthrough (see _recompute_working_array's own
        docstring on why this used to convert)."""
        if self._reference_original_array is None:
            self._reference_image_array = None
            return
        self._reference_image_array = self._reference_original_array

    def pick_pixel_color_for_stage(self, stage_id, callback):
        """Eyedropper entry point (per Tom) -- arms self.compare.
        arm_eyedropper (see that method's own docstring) for a click
        ANYWHERE on the Viewer, then resolves the click's own fractional
        position against `stage_id`'s ACTUAL INPUT, not whatever's on
        screen (per Tom: "the color picker for the key still seems to
        rely on actually pulling a pixel from the preview image, it
        should be pulling up the pixel address of the input of the key
        entry" -- the on-screen pixel reflects the FULL composited stack,
        including every stage AFTER stage_id, any active wipe-compare
        source selection, and any Highlight overlay, none of which is
        what stage_id itself actually reads). Reads self._image_array
        (the raw, full-resolution, pre-stack source -- see this module's
        own docstring) at the picked pixel, folds only the stages
        strictly BEFORE stage_id over that ONE pixel (see stack.
        running_array_before_stage's own docstring for why a single
        pixel is exactly as correct, and far cheaper, than re-grading
        the whole image), and hands the result to `callback(rgb)` as a
        plain (r, g, b) float tuple -- still in whatever encoding the
        working pipeline is in at that point (e.g. ACEScct), same as
        keyer.py's own OKLCh decode already expects. `callback` is
        simply never invoked if anything along the way doesn't resolve
        (no source image loaded, no live stack, the click landed outside
        the image, or stage_id can't be resolved -- see running_array_
        before_stage's own docstring for that last case) -- same silent-
        no-op convention every other matte/stage reference in this app
        already uses. StackPanel doesn't hold a WipeCompareWidget
        reference of its own -- panels only ever talk via getters/
        signals through main_window.py, same as set_stack_getter/
        set_highlight_getter above -- so it reaches the Viewer through
        THIS panel instead, via StackPanel.set_eyedropper_requester."""
        def _on_click_fraction(x_frac, y_frac):
            if self._image_array is None:
                return
            stack = self._stack_getter() if self._stack_getter else None
            if stack is None:
                return
            h, w = self._image_array.shape[:2]
            x = min(w - 1, max(0, int(x_frac * w)))
            y = min(h - 1, max(0, int(y_frac * h)))
            pixel = self._image_array[y:y + 1, x:x + 1, :]
            result = running_array_before_stage(stack, pixel, stage_id)
            if result is None:
                return
            callback(tuple(float(v) for v in result[0, 0]))
        self.compare.arm_eyedropper(_on_click_fraction)

    def refresh_preview(self):
        """Public trigger for 'something this panel doesn't own directly
        changed, re-render' -- used for StackPanel.stack_changed, since
        that fires on every stack mutation and this panel has no other
        way to know. Recomputes Sample After specifically (the only one
        of the three wipe sources that depends on the Stack) and always
        re-renders the wipe regardless of which source(s) are currently
        selected -- if Sample After isn't currently showing on either
        side, this is a cheap no-op render, not a wrong one.

        EDITING-TIME DOWNRES (2026-08-09, replaces the old zoom/resize-
        reload band-aid -- see this module's "PREVIEW RESOLUTION"
        comment): `_editing_settle_timer.isActive()` being True here means
        a PREVIOUS call to this method started it and it hasn't fired yet
        -- i.e. this is at least the second edit of an ongoing burst
        (a slider drag firing stack_changed on every tick), not an
        isolated single edit. Only THAT case renders from the downres
        array; a genuinely isolated edit (the common case: toggling a
        stage, picking a colorspace, adding/removing a stage) always
        renders at full quality immediately, exactly like before this
        existed -- there's no burst to protect against, so there's
        nothing to gain from degrading it, only a pointless soft frame
        followed by a redundant re-render once the settle timer fires.
        Restarting the timer on every call (regardless of which branch
        rendered) is what turns "quiet for EDIT_SETTLE_DEBOUNCE_MS" into
        the signal that a burst has ended -- see _on_editing_settled.

        GPU-ELIGIBLE STACKS ALWAYS RECOMPUTE AT SCOPES RESOLUTION
        (2026-08-09, follow-up REVISED 2026-08-10): for a stack
        gpu_stack_renderer.is_stack_gpu_eligible approves, the Viewer
        grades it live on the GPU itself (see _render_wipe/
        _array_and_gpu_stack_for_source) -- it never reads
        _current_after_array at all. The ONLY consumer of this recompute
        for such a stack is ScopesPanel (via after_image_changed), so it
        never needs native (or even EDIT_DOWNRES_FACTOR-halved)
        resolution -- it always runs against _image_array_scopes (see
        SCOPES_MAX_DIMENSION's own comment), REGARDLESS of burst-vs-
        isolated state, not just during a burst. The original 2026-08-09
        version of this fix only skipped the recompute DURING a burst,
        still paying full native-resolution cost on the first tick of
        every edit and again once a burst settled -- which sounded right
        until a real, measured case broke it: a 'lut' stage's CPU path
        (colour.LUT3D.apply()) costs 0.5-3.3 SECONDS at native/half
        preview resolution, so EVERY isolated edit (not just bursts) was
        still stalling the UI thread for seconds at a time on a stack
        with a cube in it. Recomputing at a small fixed SIZE unconditionally
        removes every one of those stall points at once, and needs no
        "catch up once settled" step afterward (see _on_editing_settled)
        since there's no higher-resolution version of THIS array to
        catch up to -- Scopes simply always looks at the same small,
        fast, representative sample, no staleness window at all."""
        burst_in_progress = self._editing_settle_timer.isActive()
        self._editing_settle_timer.start(EDIT_SETTLE_DEBOUNCE_MS)
        stack = self._stack_getter() if self._stack_getter else None
        gpu_eligible = self._gpu_stack_for_viewer(stack) is not None
        if gpu_eligible:
            self._recompute_sample_after(scopes=True)
        else:
            self._recompute_sample_after(downres=burst_in_progress)
        self._render_wipe()
        self._casting_controller.on_stack_or_image_changed()

    def _on_editing_settled(self):
        """Fires once a burst of edits has gone quiet for
        EDIT_SETTLE_DEBOUNCE_MS. If the most recent burst tick was
        downres'd (the CPU-fallback path for a GPU-INeligible stack, see
        refresh_preview), replace it with one final full-quality
        recompute now that things have stopped moving -- if it wasn't
        (an isolated single edit, or a GPU-eligible stack that's always
        at scopes resolution and never needs an "upgrade"), this is a
        no-op rather than a pointless redundant re-render of an already-
        correct (or intentionally-always-small) frame."""
        if not self._last_after_was_downres:
            return
        self._recompute_sample_after(downres=False)
        self._render_wipe()

    def _recompute_sample_after(self, downres=False, scopes=False):
        """Computes (or clears) Sample's post-stack result and notifies
        anything listening (ScopesPanel via after_image_changed) --
        deliberately does NOT touch the wipe display itself (see
        _render_wipe, which reads self._current_after_array back out
        whenever "Sample After" is one of the currently-selected
        sources). Split out from the old _render_after specifically so
        Scopes can keep tracking "the graded result" even when the wipe
        itself is currently showing Reference on both sides.

        `downres`/`scopes` are mutually exclusive (a caller passes at
        most one True) and select which of THREE independent working
        arrays + StageCache pairs to use -- native (_image_array/
        _preview_cache), EDIT_DOWNRES_FACTOR-halved (_image_array_downres/
        _preview_cache_downres, the CPU-fallback burst path for a GPU-
        INeligible stack), or SCOPES_MAX_DIMENSION-capped
        (_image_array_scopes/_preview_cache_scopes, what a GPU-eligible
        stack always uses -- see refresh_preview's own comment). Three
        SEPARATE StageCache instances, not one cache asked about three
        differently-shaped arrays under the same generation-counter key
        -- see _image_array_downres's own __init__ comment for why that
        would hand back wrongly-shaped cached blocks."""
        if scopes:
            working_array = self._image_array_scopes
            cache = self._preview_cache_scopes
        elif downres:
            working_array = self._image_array_downres
            cache = self._preview_cache_downres
        else:
            working_array = self._image_array
            cache = self._preview_cache
        if self._image_array is None or working_array is None:
            self._set_current_after_array(None)
            self._last_after_was_downres = False
            return
        stack = self._stack_getter() if self._stack_getter else None
        if stack is None:
            self._set_current_after_array(None)
            self._last_after_was_downres = False
            return
        # An empty stack is a valid thing to preview (per Tom: "you
        # should be able to preview an empty stack") -- NOT treated the
        # same as "no stack at all" above. apply_stack_to_image_array's
        # own docstring already guarantees an empty stack returns the
        # image unchanged, so this just falls through to the normal
        # apply path below rather than special-casing it.
        try:
            result = apply_stack_to_image_array(
                stack, working_array,
                cache=cache, input_key=self._image_array_generation)
        except Exception as e:  # noqa: BLE001 -- surface any apply failure to the user
            self.status_label.setText(f"Failed to apply stack: {e}")
            self._set_current_after_array(None)
            self._last_after_was_downres = False
            return
        self.status_label.setText("")
        self._last_after_was_downres = downres
        self._set_current_after_array(result)

    def _highlighted_after_array_or_none(self):
        """None unless a matte-producing stage is currently Highlight-
        toggled (self._highlight_getter, see set_highlight_getter) AND
        that stage actually resolved to a matte in the most recent run
        that produced self._current_after_array -- both "no highlight
        toggled" and "the toggled stage was removed/disabled/hasn't run"
        fall back to None here, same silent no-error convention the rest
        of the matte system already uses everywhere (see stack.py's
        docstring, "MATTE CHANNELS"). Picks whichever StageCache tier
        actually produced self._current_after_array -- see
        _recompute_sample_after's own tier-selection comment, mirrored
        here via self._last_after_was_downres rather than duplicating
        that selection logic a third way."""
        if self._highlight_getter is None:
            return None
        highlight_stage_id = self._highlight_getter()
        if highlight_stage_id is None or self._current_after_array is None:
            return None
        cache = self._preview_cache_downres if self._last_after_was_downres else self._preview_cache
        matte = cache.last_mattes().get(highlight_stage_id)
        if matte is None:
            return None
        return _compose_highlight_overlay(self._current_after_array, matte)

    def _gpu_stack_for_viewer(self, stack):
        """`stack` if the Viewer should grade it on the GPU, else None:
        non-empty, GPU-eligible, and no matte Highlight showing for one of
        its stages -- the Highlight overlay is composited from the CPU
        path's mattes (_highlighted_after_array_or_none), so while it's
        on the Viewer stays on the CPU path, as it always did before
        matte stages could render on the GPU."""
        if stack is None or not stack.stages or not gpu_stack_renderer.is_stack_gpu_eligible(stack):
            return None
        highlight_id = self._highlight_getter() if self._highlight_getter else None
        if highlight_id is not None and any(s.id == highlight_id for s in stack.stages):
            return None
        return stack

    def _array_and_gpu_stack_for_source(self, key, gpu_stack):
        """(array, stage_for_widget_to_grade_or_None, placeholder_message)
        for one of WIPE_SOURCE_CHOICES' three keys -- shared by the
        per-side Left/Right combos (wipe on) and the single Show combo
        (wipe off) in _render_wipe, so all three ever resolve a given
        source the exact same way.

        `gpu_stack` is the live Stack, already confirmed GPU-eligible
        (see gpu_stack_renderer.is_stack_gpu_eligible) and non-empty, or
        None if the stack isn't eligible/is empty -- callers pass the
        SAME value regardless of which key is being resolved; only
        SAMPLE_AFTER actually uses it (SAMPLE_BEFORE/REFERENCE are never
        stack-graded, by definition -- see this module's own docstring).
        When `gpu_stack` is given, SAMPLE_AFTER hands back the RAW
        pre-stack array (self._image_array, always full native
        resolution -- no downres coupling needed here, since GPU grading
        is fast regardless of an edit burst, see refresh_preview's own
        EDIT_DOWNRES_FACTOR comment, which still applies to the CPU-
        fallback case below) for WipeCompareWidget to grade on the GPU
        itself; when `gpu_stack` is None, it hands back the already-CPU-
        computed self._current_after_array instead (see
        _recompute_sample_after, still the source of truth for Scopes
        and for any GPU-ineligible stack).

        MATTE HIGHLIGHT (per Tom: "a way to preview the mask similar to
        resolve's highlight mode"): the CPU branch below is the single
        shared dispatch point Left/Right/Show all funnel through for
        this source, so compositing the highlight HERE (rather than in
        _recompute_sample_after) means it applies correctly regardless
        of wipe layout with no per-side special-casing, and deliberately
        never touches self._current_after_array itself -- Scopes and
        export keep seeing the real graded array; only what gets handed
        to the WIDGET for display is composited. Only ever relevant on
        this CPU branch: a highlight is only meaningful when the
        highlighted stage is an enabled matte-producing stage actually
        in the CURRENT stack, and any such stack is already forced onto
        the CPU path (see stack.MATTE_PRODUCING_KINDS' own docstring on
        why), so `gpu_stack` is already guaranteed None whenever a
        highlight could actually resolve to anything."""
        if key == WIPE_SOURCE_SAMPLE_BEFORE:
            if self._image_array is None:
                return None, None, "Load a source image to preview"
            return self._image_array, None, ""
        if key == WIPE_SOURCE_SAMPLE_AFTER:
            if self._image_array is None:
                return None, None, "Load a source image to preview"
            if gpu_stack is not None:
                return self._image_array, gpu_stack, ""
            if self._current_after_array is None:
                return None, None, "Add stages to the Stack to preview"
            highlighted = self._highlighted_after_array_or_none()
            if highlighted is not None:
                return highlighted, None, ""
            return self._current_after_array, None, ""
        if key == WIPE_SOURCE_REFERENCE:
            if self._reference_image_array is None:
                return None, None, "Load a Reference image to preview"
            return self._reference_image_array, None, ""
        return None, None, ""  # pragma: no cover -- the combos only ever offer the three keys above

    def _render_wipe(self):
        """Central place that pushes whatever's currently selected (Left/
        Right when the wipe's on, just Right when it's off -- see this
        module's docstring on why there's no separate "Show" state) into
        the WipeCompareWidget -- called after literally anything that
        could change what should be on screen: a stack edit
        (refresh_preview), a new Source/Reference image loading, a
        Left/Right menu selection changing, or Enable Wipe being
        toggled. Always recomputes both
        potentially-visible sources (even in single mode, only Right
        matters, but it's the same cheap dispatch either way) rather than
        trying to track which one specifically changed -- see
        _array_and_gpu_stack_for_source, which is O(1) dispatch, not a
        re-decode or a real compute (the actual GPU grading, if any,
        happens lazily inside WipeCompareWidget's own paintGL)."""
        wipe_enabled = self._wipe_enabled
        self.compare.set_wipe_enabled(wipe_enabled)

        stack = self._stack_getter() if self._stack_getter else None
        gpu_stack = self._gpu_stack_for_viewer(stack)

        if wipe_enabled:
            left_array, left_gpu_stack, _left_placeholder = \
                self._array_and_gpu_stack_for_source(self._wipe_left_key, gpu_stack)
            right_array, right_gpu_stack, right_placeholder = \
                self._array_and_gpu_stack_for_source(self._wipe_right_key, gpu_stack)
            self.compare.set_after_placeholder(right_placeholder)
            self.compare.set_before(left_array, gpu_stack=left_gpu_stack)
            self.compare.set_after(right_array, gpu_stack=right_gpu_stack)
        else:
            array, side_gpu_stack, placeholder = \
                self._array_and_gpu_stack_for_source(self._wipe_right_key, gpu_stack)
            self.compare.set_after_placeholder(placeholder)
            self.compare.set_before(None)
            self.compare.set_after(array, gpu_stack=side_gpu_stack)

    def _on_wipe_enabled_toggled(self, checked):
        """Wired to every "Enable Wipe" QAction built by populate_menu (one
        per menu instance) -- updates the shared state, then mirrors the
        new checked-state into every OTHER instance's action too (blocked,
        so this doesn't recurse back into this same handler), so the
        corner "..." menu and the top-level Viewer menu-bar entry never
        show a stale/contradictory checkmark for each other."""
        self._wipe_enabled = checked
        self._update_viewer_readout()
        for action in self._wipe_enabled_actions:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
        self._render_wipe()

    def shutdown_casting(self):
        """Called from MainWindow.closeEvent -- stops every active sink
        (today: just the Independent Viewer window, if open; later: a
        DeckLink sink too) before the app quits, rather than leaving a
        stray window (or hardware output) open after LUTier itself has
        already gone away."""
        self._casting_controller.stop_all()

    def _on_cast_toggled(self, checked):
        """Wired to every "Open Independent Viewer Window" QAction built
        by populate_menu -- same cross-instance sync convention as
        _on_wipe_enabled_toggled. self._independent_viewer_sink tracks
        the ONE AirPlayOutputSink instance this menu item owns, so
        toggling off can stop exactly that sink (see CastingController.
        stop_sink) without disturbing any OTHER sink that might be active
        at the same time (a future DeckLink toggle, see casting_
        controller.py's own "fork 3 ways" docstring) -- opening pushes an
        initial frame immediately (see CastingController.start_sink);
        closing tears the native window down."""
        if checked:
            self._independent_viewer_sink = AirPlayOutputSink()
            self._casting_controller.start_sink(self._independent_viewer_sink)
        else:
            self._casting_controller.stop_sink(self._independent_viewer_sink)
            self._independent_viewer_sink = None
        for action in self._casting_actions:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

    def _on_wipe_left_selected(self, key):
        """Wired to every "Left Image" submenu's QActionGroup (one per menu
        instance) -- same cross-instance sync idea as
        _on_wipe_enabled_toggled, just for a radio-style choice instead of
        a checkbox: only the action matching `key` ends up checked, in
        every submenu instance, not just the one that was clicked."""
        self._wipe_left_key = key
        for action, action_key in self._wipe_left_actions:
            action.blockSignals(True)
            action.setChecked(action_key == key)
            action.blockSignals(False)
        self._render_wipe()

    def _on_wipe_right_selected(self, key):
        """Mirrors _on_wipe_left_selected for the "Right Image" submenu."""
        self._wipe_right_key = key
        for action, action_key in self._wipe_right_actions:
            action.blockSignals(True)
            action.setChecked(action_key == key)
            action.blockSignals(False)
        self._render_wipe()

    def _on_async_scopes_result(self, array, token):
        if token == self._scopes_worker.token:  # not cancelled since it was submitted
            self._set_current_after_array(array)

    def _set_current_after_array(self, array):
        """Single place that updates the "last after result" and notifies
        anything listening (ScopesPanel) -- so every return path in
        _recompute_sample_after (success, empty-stack, or apply failure)
        keeps scopes in sync with the current graded result, independent
        of whether "Sample After" is one of the sources actually showing
        in the wipe right now."""
        self._current_after_array = array
        self.after_image_changed.emit(array)

    def get_current_after_array(self):
        """The last successfully rendered 'after' array (post-stack, at
        preview resolution), or None if nothing's currently showing. For a
        panel that attaches after the fact (or wants a pull instead of
        subscribing to after_image_changed) to read the current state
        without waiting for the next change."""
        return self._current_after_array

    def render_full_resolution_after(self, stack):
        """Reload the source image at full resolution, then apply `stack`
        -- the actual computation shared by export_still() (its own
        file-picker flow, below) and MainWindow's Export dialog "Export
        Still" checkbox (per Tom), so both produce an identically
        processed still without duplicating the reload/apply sequence.
        Reloads the source file at full resolution rather than reusing
        the (deliberately downsampled) preview array -- see this module's
        docstring. Raises whatever load_image_as_array/
        apply_stack_to_image_array itself raises on failure -- callers
        surface that however fits their own UI. Caller is responsible for
        the "a source image is loaded" precondition (see
        has_sample_image) -- this assumes self._current_path is already
        set."""
        full_res = load_image_as_array(self._current_path, max_dimension=None)
        return apply_stack_to_image_array(stack, full_res)

    def render_full_resolution_after_for_capture(self, stack):
        """Same idea as render_full_resolution_after, but works for
        whichever kind of Sample is actually loaded -- a still (delegates
        straight to render_full_resolution_after, the OIIO reload path)
        or a parked VIDEO frame, which has no "reload from path" OIIO
        could take (a video container isn't a still-image format) --
        re-decodes the CURRENT frame at full native resolution via the
        already-open VideoSource instead (seek_to_frame with no
        max_dimension cap, same call scrubbing/stepping already use).
        Used by stack_memories_panel.py's Stills Gallery capture-at-save
        (Phase 3 follow-up, per Tom: "also capture full canvas-res
        still") -- NOT used by Export Still/the Export dialog's "Export
        Still" checkbox, which only ever expect a still-backed Sample and
        keep using render_full_resolution_after directly. Raises
        RuntimeError if the video frame can't be re-decoded (source
        closed/end of stream) -- same "let the caller decide how to
        surface it" convention render_full_resolution_after itself
        follows for its own failures."""
        if self._video_source is not None:
            full_res = self._video_source.seek_to_frame(self._video_frame_index)
            if full_res is None:
                raise RuntimeError("Failed to re-decode the current video frame")
            return apply_stack_to_image_array(stack, full_res)
        return self.render_full_resolution_after(stack)

    def export_still(self):
        """Save the currently previewed "after" result as a real still
        image file, at the ORIGINAL image's full resolution -- not the
        downsampled preview. Same source-of-truth as _render_after (the
        live stack, via get_stack()) so what gets saved always matches
        what's on screen, just at full quality instead of preview
        quality."""
        if not self.has_sample_image():
            QMessageBox.warning(self, "No image", "Load a source image first.")
            return

        stack = self._stack_getter() if self._stack_getter else None
        if stack is None or not stack.stages:
            QMessageBox.warning(
                self, "Empty stack",
                "Add stages to the Stack first.")
            return

        default_name = Path(self._current_path).stem + "_graded.png"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export Still", default_name,
            "PNG (*.png);;TIFF (*.tif *.tiff);;JPEG (*.jpg *.jpeg)",
        )
        if not out_path:
            return

        try:
            result = self.render_full_resolution_after(stack)
        except Exception as e:  # noqa: BLE001 -- surface any decode/apply failure to the user
            QMessageBox.critical(self, "Export failed", f"Couldn't process the image: {e}")
            return

        try:
            export_image_array_to_file(result, out_path)
        except Exception as e:  # noqa: BLE001 -- surface any write failure to the user
            QMessageBox.critical(self, "Export failed", f"Couldn't save the file: {e}")
            return

        QMessageBox.information(
            self, "Exported",
            f"Saved {result.shape[1]}x{result.shape[0]} still to:\n{out_path}")
