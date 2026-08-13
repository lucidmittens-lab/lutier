"""
wipe_compare_widget.py -- an interactive before/after image compare: one
overlapping view with a draggable vertical divider, instead of two
side-by-side panels. Which source is on the left vs. right (Sample
Before/Sample After/Reference) is entirely sample_image_panel.py's call
(see its Left/Right "..." menu submenus) -- this widget just draws
whatever arrays it's handed via set_before/set_after, with no opinion on
what they actually are. There USED to be a fixed "BEFORE"/"AFTER" corner
label overlay, but with three independently-pickable sources per side that
label was wrong as often as it was right (per Tom) -- removed entirely
rather than taught to ask sample_image_panel.py what's actually loaded on
each side. A separate "Color: Unmanaged"/"Color: X -> Y" chip overlay
(fed by sample_image_panel.py's own _update_color_readout) existed the
same way for a while, but was removed outright per direct feedback (it
was in the way while iterating on the Viewer) -- see RUNNING.md for that
history if a similar per-source readout is wanted again later; this
widget doesn't track colorspace at all now.

FIRST PASS on "make the before/after more attractive and wipeable" --
functional and interactive, but the visual styling (colors, handle shape)
is a starting point for iteration, not a settled opinion. Easy knobs if
the look needs to change: HANDLE_COLOR, HANDLE_WIDTH, HANDLE_GRIP_RADIUS.

GPU-ACCELERATION PROJECT, STAGE 5 (2026-08-09) -- this widget is now the
actual GPU-resident rendering surface, not just a QOpenGLWidget hosting
QPainter (see Stage 1's note below, now superseded). set_before/set_after
take raw (H,W,3) float32 numpy arrays instead of pre-rendered QPixmaps;
set_after optionally takes a `gpu_stack` (a GPU-eligible stack.Stack, see
gpu_stack_renderer.is_stack_gpu_eligible) to apply ON THE GPU, inside this
widget's own paintGL, rather than sample_image_panel.py precomputing the
graded result on the CPU first. Per Tom: use Lanczos scaling, and keep the
whole native-resolution-source -> screen mapping as ONE concatenated scale
operation -- see gpu_lanczos_compositor.py's own docstring for the full
design (separable 2-pass Lanczos-3, replacing the old QPainter.drawPixmap
bilinear scaling).

THREE LAYERS, each owning a different concern, on purpose:
- gpu_stack_renderer.GpuStackRenderer: runs a stack's enabled stages
  (native resolution, ping-ponged) -- the actual color math.
- gpu_lanczos_compositor.WipeCompositor: resamples native-resolution
  "before"/"after" into screen space and picks between them per the wipe
  divider -- pure spatial/compositing math, no color-math awareness.
- This widget: owns both of the above, decides WHEN to re-run the
  (expensive) color chain vs. WHEN to just re-composite the (cheap)
  already-graded result -- see _AFTER_DIRTY tracking in set_after/paintGL
  below. Mouse interaction (wipe drag, zoom/pan) stays exactly as before,
  operating purely on _image_rect()'s geometry -- none of that needed to
  change for this rewrite, which is why it isn't repeated here.

CPU FALLBACK, still real: a stack containing a GPU-ineligible "lut"/
"printer_lights" stage (see gpu_stack_renderer.py's own docstring for
what makes either only PARTIALLY eligible) or a fork isn't GPU-eligible
-- sample_image_panel.py computes THAT case's graded result on
the CPU exactly as before (apply_stack_to_image_array) and calls
set_after(cpu_result, gpu_stack=None) -- this widget just uploads and
displays it directly, still benefiting from the new GPU-resident Lanczos
compositing/scaling even though the color math itself stayed on the CPU.

STAGE 1 (2026-08-08, superseded by the above) -- this widget first became
a QOpenGLWidget while paintEvent stayed plain QPainter drawing, fed
CPU-computed QPixmaps, as a deliberately isolated first step (rendering-
surface swap only, no color-math change) -- see
GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 1. Left here for history since
the reasoning (reparent-safety via isValid(), AA_ShareOpenGLContexts)
still applies to this file, just layered with real GL rendering now.

Drag feel (grab precision, handle visibility), zoom/pan, the Stage 1
QOpenGLWidget swap, and now the full GPU-resident rendering pipeline have
all been exercised on real macOS hardware by this point, not just syntax-
checked -- if a future change to this file touches the GL pipeline again,
please confirm on real hardware again before treating it as done, same
discipline as always: this is exactly the kind of thing headless test
coverage alone won't catch.

ELEVENTH PASS (explicit background fill, 2026-07-25) -- the space in the
panel body AROUND the actual image (the letterboxed gutter, or the full
canvas before any image is loaded) must never be left to whatever an
ancestor widget happens to already have painted underneath (see RUNNING.md
#44/#46 for two real bugs that came from exactly that assumption) -- this
widget owns its own background outright. In the GPU pipeline, that's
WipeCompositor's own uBackgroundColor (palette(base), passed in from here)
filling everything outside uImageRect; HANDLE_COLOR/PLACEHOLDER_BG
below stay hardcoded (they're drawn on top of arbitrary image content, not
part of the app's theme-following chrome), same reasoning as always.
"""
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QPalette
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QSizePolicy

import gpu_lanczos_compositor
import gpu_stack_renderer
import gpu_shader_prototype as gsp
from stack import _stage_fingerprint

HANDLE_COLOR = QColor(255, 255, 255, 230)
HANDLE_WIDTH = 2
HANDLE_GRIP_RADIUS = 9
EMPTY_TEXT_COLOR = QColor(150, 150, 150)
PLACEHOLDER_BG = QColor(40, 40, 40)

# Zoom/pan (per Tom) -- ZOOM_STEP is the multiplier applied per "notch"
# of scroll wheel (angleDelta().y() normalized to /120, the traditional
# one-notch unit -- see wheelEvent), so the step size is independent of
# whatever units/resolution a given mouse or trackpad actually reports.
# MIN/MAX_ZOOM bound how far in/out you can go relative to "fit" (1.0) --
# wide enough for real use, not so wide that a runaway scroll lands
# somewhere useless (a single pixel, or so far out the image is a speck).
ZOOM_STEP = 1.1
MIN_ZOOM = 0.1
MAX_ZOOM = 20.0


# OpenGL 4.1 core profile is REQUIRED app-wide -- see main_window.main()/
# splash_launcher.py's own QSurfaceFormat.setDefaultFormat() call for why
# this can't be a per-widget setFormat() here instead: this widget's own
# shaders (gpu_shader_prototype.py's fragment template, gpu_lanczos_
# compositor.py's) are GLSL 400 (#version 400 core), needing a context
# that supports it, but a DIFFERENT format on just this ONE widget than
# whatever the rest of the app's shared GL context group uses (notably
# lut_viewer_3d.py's pyqtgraph GLViewWidget) breaks AA_ShareOpenGLContexts
# sharing outright on macOS -- confirmed on real hardware: doing that
# produced "Could not create NSOpenGLContext with shared context, falling
# back to unshared context," which promptly broke the Graph panel's own
# GL rendering (GL_INVALID_OPERATION on its next glClearColor). The whole
# app needs to agree on ONE format, requested before QApplication exists,
# same timing constraint AA_ShareOpenGLContexts itself already has.


def _array_fingerprint(array):
    """Identity, not value equality -- every producer in this app hands
    over a genuinely NEW array object when its content actually changes
    (SampleImagePanel's _image_array/_current_after_array are only ever
    reassigned, never mutated in place, same discipline stack.py's own
    StageCache leans on for its identity-based fingerprinting) and reuses
    the SAME object across repaints that don't change anything (e.g. just
    dragging the wipe divider) -- so `id()` is the right, cheap check
    here, not a real (and for a multi-megapixel array, expensive) value
    comparison."""
    return id(array) if array is not None else None


def _stack_fingerprint_or_none(stack):
    if stack is None:
        return None
    return tuple(_stage_fingerprint(s) for s in stack.stages)


class _SideState:
    """GPU state for one side (before or after) of the wipe -- both
    support optional stack grading (see set_before/set_after) since
    "Sample After" is a valid pick for EITHER side, not just the
    conventional right/after slot (see sample_image_panel.py's
    WIPE_SOURCE_CHOICES) -- a shared helper instead of duplicating this
    upload/grade/cache logic once per side. Real GL calls only ever
    happen inside ensure_texture/clear_gl_resources, called from
    WipeCompareWidget.paintGL where this widget's own context is
    guaranteed current."""

    def __init__(self):
        self.array = None
        self.gpu_stack = None
        self.native_texture = None
        self.native_texture_size = None
        self.graded_texture = None
        self.uploaded_array_fingerprint = None
        self.applied_stack_fingerprint = None

    def set(self, array, gpu_stack):
        self.array = array
        self.gpu_stack = gpu_stack

    def ensure_texture(self, stack_renderer):
        """Uploads self.array (only if the ARRAY itself changed -- see
        _array_fingerprint) and, INDEPENDENTLY, re-runs the GPU color
        chain (only if the STACK's fingerprint changed -- see
        _stack_fingerprint_or_none). Returns (graded_texture, (w, h)) or
        (None, None).

        These two checks used to be combined into ONE fingerprint tuple
        (array_fp, stack_fp), gated together -- a real, reproduced bug
        (found 2026-08-10 while chasing a "still feels slow" report after
        the earlier CPU-recompute fix): since a live slider drag changes
        the STACK fingerprint on every single tick, that combined check
        was ALSO true on every tick, which meant the native-resolution
        SOURCE array -- completely unchanged, same pixels every time --
        was being deleted and re-uploaded to the GPU from scratch
        (glTexImage2D of a full native-resolution RGBA32F texture, e.g.
        ~130MB for a 4K image) on every single edit, not just re-running
        the (cheap) color chain against the ALREADY-uploaded texture.
        That upload cost was the actual remaining bottleneck, not
        anything about the color math itself."""
        array_fp = _array_fingerprint(self.array)
        stack_fp = _stack_fingerprint_or_none(self.gpu_stack)
        array_changed = array_fp != self.uploaded_array_fingerprint

        import OpenGL.GL as gl
        if array_changed:
            if self.native_texture is not None:
                gl.glDeleteTextures([self.native_texture])
            self.native_texture = None
            self.native_texture_size = None
            self.graded_texture = None
            self.uploaded_array_fingerprint = array_fp
            self.applied_stack_fingerprint = None  # force the chain below to re-run too

            if self.array is None:
                return None, None
            tex, w, h = gsp.upload_rgba32f_texture(self.array)
            self.native_texture, self.native_texture_size = tex, (w, h)

        if self.native_texture is None:
            return None, None

        if stack_fp != self.applied_stack_fingerprint or self.graded_texture is None:
            self.applied_stack_fingerprint = stack_fp
            if self.gpu_stack is None:
                self.graded_texture = self.native_texture
            else:
                w, h = self.native_texture_size
                self.graded_texture = stack_renderer.render_chain(self.gpu_stack, self.native_texture, w, h)

        return self.graded_texture, self.native_texture_size

    def clear_gl_resources(self):
        if self.native_texture is not None:
            import OpenGL.GL as gl
            gl.glDeleteTextures([self.native_texture])
        self.native_texture = None
        self.native_texture_size = None
        self.graded_texture = None
        self.uploaded_array_fingerprint = None
        self.applied_stack_fingerprint = None


class WipeCompareWidget(QOpenGLWidget):
    """set_before(array) / set_after(array, gpu_stack=None) to update
    images -- (H,W,3) float32 numpy arrays, domain [0,1] (or slightly
    beyond, e.g. an unclipped ACES DRT result -- the GPU pipeline clamps
    to [0,1] at display time same as the old CPU path did). Both are
    expected to be the same underlying image at the same size (one
    processed, one not) -- drawn into the same scaled/centered rect so the
    wipe lines up pixel-for-pixel. Not meant to align mismatched images
    (Reference vs. Sample CAN differ in native resolution/aspect ratio --
    each gets its own independent scale-to-fit, same tolerance the old
    QPainter path already had, see gpu_lanczos_compositor.WipeCompositor.
    render's own docstring)."""

    wipe_changed = Signal(float)
    # Fires on every actual size change (not just paint). Emitted from
    # resizeEvent, not paintEvent, since a resize is the only thing that
    # changes this widget's own geometry. SampleImagePanel doesn't
    # subscribe to this as of the 2026-08-09 preview-resolution rework
    # (Sample/Reference now always load at native resolution -- see that
    # module's "PREVIEW RESOLUTION" comment; _image_rect below already
    # scales whatever it's handed to fit the current panel size on its
    # own, with nothing else needing to react to a resize) -- kept as
    # part of this widget's own public API regardless, same as any normal
    # Qt widget signaling its own geometry changes.
    resized = Signal()
    # Same idea as `resized`, for zoom (per Tom, following up on the zoom/
    # pan feature) -- fired from wheelEvent/reset_view only when
    # self._zoom actually changes, not on every wheel tick that happens to
    # be clamped at MIN_ZOOM/MAX_ZOOM already. Also unsubscribed-from by
    # SampleImagePanel as of the same 2026-08-09 rework as `resized` above,
    # for the same reason -- kept for the same "still a reasonable part of
    # this widget's own public API" reasoning, and because zoom() itself
    # (used by whatever needs to know the current zoom level) not
    # changing without this signal also firing is exactly the kind of
    # invariant this widget's own tests should keep verifying regardless
    # of who currently listens.
    zoom_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Guards paintEvent/paintGL against running before the rest of
        # __init__ below has actually set this widget's own attributes --
        # confirmed on real hardware that this genuinely happens: Qt/
        # PySide can deliver a real paint callback into an ALREADY-
        # constructed WipeCompareWidget instance while a COMPLETELY
        # UNRELATED widget elsewhere in the same window is still being
        # built (reproduced via stack_panel.py's _StackListWidget.__init__
        # -> QListWidget.__init__ -> Qt's own internal event delivery),
        # and that callback can land on a Python wrapper that hasn't
        # (yet, or anymore) had its own __init__ attributes set --
        # `AttributeError: 'WipeCompareWidget' object has no attribute
        # '_after_source_array'` was the real, reproduced symptom, not a
        # hypothetical. Set True as the LAST line of __init__ below.
        self._constructed = False
        self._after_placeholder_text = "Load a LUT to see the result"
        self._wipe_fraction = 0.5
        self._dragging = False
        # See set_wipe_enabled -- when False, paintEvent draws only
        # `_after` full-panel (whatever single source sample_image_panel.py
        # currently has selected), no divider/handle. `_before` is simply
        # ignored while this is False, not cleared -- re-enabling the wipe
        # doesn't lose whatever was on the left side. Defaults True here
        # (this widget's own out-of-the-box behavior) -- the app's actual
        # starting state is sample_image_panel.py's own call, which starts
        # its OWN _wipe_enabled at False per direct feedback and pushes
        # that down via set_wipe_enabled on first render; this widget can
        # still be constructed/tested standalone with wipe interaction
        # working immediately, same as before that app-level default
        # changed.
        self._wipe_enabled = True
        # Zoom/pan (per Tom) -- zoom is a multiplier on top of the normal
        # "fit to panel" scale _image_rect already computes (1.0 = fit,
        # unchanged from before this existed), pan is a widget-space
        # pixel offset added to the centered position. Deliberately NOT
        # reset by set_before/set_after: those fire on every single live
        # stack edit (see sample_image_panel.py's refresh_preview), so
        # resetting here would mean zooming in to inspect a detail while
        # grading gets thrown away on the very next slider tick -- see
        # reset_view for the actual, explicit way to get back to "fit".
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._panning = False
        self._pan_start_mouse = None
        self._pan_start_offset = None
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ---- GPU pipeline state (Stage 5) --------------------------------
        # Real GL objects (textures, the renderer/compositor instances)
        # only ever get created/touched inside initializeGL/paintGL, where
        # this widget's own context is guaranteed current -- everything
        # here is None until then. _SideState (see its own docstring)
        # tracks "what's currently cached" per side so paintGL can tell a
        # real content change (re-upload/re-grade needed) apart from a
        # repaint triggered by something else entirely (wipe drag, pan,
        # zoom, an unrelated Qt event) that should only re-run the cheap
        # Lanczos/composite step.
        self._gpu_stack_renderer = None
        self._wipe_compositor = None
        self._before_state = _SideState()
        self._after_state = _SideState()
        self._constructed = True

    def set_before(self, array, gpu_stack=None):
        """`array`/`gpu_stack`: see set_after -- symmetric, since "Sample
        After" (see sample_image_panel.py's WIPE_SOURCE_CHOICES) is a
        valid pick for EITHER side, not just the conventional after/right
        slot."""
        self._before_state.set(array, gpu_stack)
        self.update()

    def set_after(self, array, gpu_stack=None):
        """`array`: the array to display, OR (if `gpu_stack` is given) the
        PRE-stack source for the GPU chain to grade. `gpu_stack`: a
        GPU-eligible stack.Stack (see gpu_stack_renderer.
        is_stack_gpu_eligible -- caller's job to check) to apply on the
        GPU inside this widget's own paintGL, or None to show `array`
        exactly as given (already fully processed on the CPU, or nothing
        to process at all)."""
        self._after_state.set(array, gpu_stack)
        self.update()

    def set_after_placeholder(self, text):
        """Message shown in the "after" area when set_after(None) --
        lets callers explain WHY there's nothing there (no LUT loaded vs.
        an empty concatenate chain are different situations that call for
        different guidance)."""
        self._after_placeholder_text = text
        self.update()

    def read_back_after_frame(self):
        """On-demand CPU readback of whatever the 'after' side is
        CURRENTLY showing on screen -- i.e. the exact same pixels the
        Viewer panel itself just painted, not a separate computation.
        Built for casting_controller.py (see its own docstring on the
        "fork 3 ways from one shared source: Viewer panel, Independent
        Viewer window, future DeckLink hardware output" design) -- Tom's
        own direct request, replacing an earlier design that ran its own
        independent CPU stack.apply_stack_to_image_array recompute.

        Deliberately reads _after_state.graded_texture, not
        _after_state.array -- this is correct for BOTH the GPU-eligible
        and GPU-ineligible case with no separate branch needed: for a
        GPU-ineligible stack, sample_image_panel.py already CPU-graded
        the array before calling set_after with gpu_stack=None, and
        _SideState.ensure_texture's own logic then sets
        graded_texture = native_texture verbatim (no further GPU
        transform applied) -- so graded_texture is always "the final
        graded result," regardless of which path produced it.

        Returns an (H, W, 3) float32 numpy array, or None if there's
        nothing to read back yet (no image loaded, or paintGL hasn't run
        at least once since set_after was last called). NOT clamped to
        [0,1] -- same "don't silently discard legitimate range" rule as
        the GPU pipeline's own render_chain (an HDR ACES DRT result can
        legitimately exceed it); callers needing a clamped result (see
        video_output_sink.VideoFrame's own docstring on why this
        interface doesn't clamp either) do it themselves downstream.

        Called ON DEMAND from OUTSIDE the normal paint cycle (a QTimer
        callback in casting_controller.py, not from paintGL) -- this
        widget's GL context is only automatically current during Qt's own
        paintGL callback, so makeCurrent()/doneCurrent() bracket the real
        GL calls here, unlike every other GL call in this file (all of
        which run from inside paintGL, where that's already guaranteed).
        Deliberately does NOT touch paintGL/_paint_gl_impl's own code
        path at all -- this method existing costs the interactive-editing
        hot path nothing whatsoever when no cast/hardware-output consumer
        is polling it.

        Checks _after_state.array (not just graded_texture) for None --
        a real, found-by-testing gap: _paint_gl_impl returns EARLY,
        before ever calling ensure_texture, whenever _image_rect() is
        None (nothing to draw at all, e.g. right after clear()) -- so
        _after_state's cached GL texture handles can go stale (still
        holding the LAST real frame's texture ID) once there's nothing
        left to display, since ensure_texture -- the only place that
        clears them -- never runs again. _after_state.array itself,
        however, IS updated immediately by clear()/set_after(None, ...)
        (no paintGL round trip needed for that part), so checking it
        directly reflects the real current intent rather than trusting
        possibly-stale cached GL state."""
        if not getattr(self, "_constructed", False) or not self.isValid():
            return None
        if self._after_state.array is None:
            return None
        texture = self._after_state.graded_texture
        size = self._after_state.native_texture_size
        if texture is None or size is None:
            return None

        import OpenGL.GL as gl
        w, h = size
        self.makeCurrent()
        try:
            fbo = gl.glGenFramebuffers(1)
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
            gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, texture, 0)
            pixels = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
            gl.glDeleteFramebuffers(1, [fbo])
        finally:
            self.doneCurrent()

        return np.frombuffer(pixels, dtype=np.float32).reshape(h, w, 4)[..., :3].copy()

    def clear(self):
        self._before_state.set(None, None)
        self._after_state.set(None, None)
        self.update()

    def set_wipe_enabled(self, enabled):
        """Toggle between the divider-based before/after compare (True,
        the original/default behavior) and a plain single-image view
        (False) showing only whatever's currently set via set_after --
        see sample_image_panel.py's "Enable Wipe" entry in the "..."
        menu. `_before` is left as-is either way (see __init__), so
        flipping this back on doesn't require re-supplying the left
        side."""
        if self._wipe_enabled == enabled:
            return
        self._wipe_enabled = enabled
        self.update()

    def wipe_fraction(self):
        return self._wipe_fraction

    def zoom(self):
        """1.0 = fit (the original, only-ever behavior before zoom/pan
        existed)."""
        return self._zoom

    def set_wipe_fraction(self, fraction):
        self._wipe_fraction = max(0.0, min(1.0, fraction))
        self.update()
        self.wipe_changed.emit(self._wipe_fraction)

    def reset_wipe(self):
        self.set_wipe_fraction(0.5)

    def reset_view(self):
        """Resets zoom/pan back to "fit" -- the middle-mouse-button
        double-click gesture (per Tom). Deliberately a SEPARATE gesture
        from the existing left-button double-click above (reset_wipe) --
        zoom/pan and the wipe divider are two independent things a user
        might want to reset without disturbing the other, and middle vs.
        left button is exactly how this widget already keeps panning
        (middle-drag) from ever conflicting with the wipe drag
        (left-drag, see mousePressEvent)."""
        zoom_changed = self._zoom != 1.0
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()
        if zoom_changed:
            self.zoom_changed.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.reset_view()
            return
        self.reset_wipe()

    def wheelEvent(self, event):
        """Scroll to zoom (per Tom), anchored on the cursor -- the image
        point currently under the cursor stays under it as the zoom
        level changes, the standard behavior in image viewers/DCC tools
        (zooming around the widget's center regardless of where the
        cursor actually is would feel wrong the moment you're not
        centered)."""
        old_rect = self._image_rect()
        if old_rect is None or old_rect.width() <= 0 or old_rect.height() <= 0:
            return
        cursor = event.position()
        # Cursor's position as a FRACTION of the current image rect
        # (0/1 = left/right edge) -- kept fixed across the zoom change,
        # rather than the absolute pixel position, since the rect itself
        # is about to change size. Extrapolates harmlessly (a fraction
        # outside [0,1]) if the cursor is over the letterboxed gutter
        # rather than the image itself.
        rel_x = (cursor.x() - old_rect.left()) / old_rect.width()
        rel_y = (cursor.y() - old_rect.top()) / old_rect.height()

        # angleDelta().y() is in eighths of a degree, 120 per traditional
        # "one wheel notch" -- normalizing to notches keeps the zoom step
        # consistent regardless of whatever units/resolution a given
        # mouse or trackpad actually reports.
        notches = event.angleDelta().y() / 120.0
        if notches == 0:
            return
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * (ZOOM_STEP ** notches)))
        if new_zoom == self._zoom:
            return
        self._zoom = new_zoom

        # Where that same relative point landed after the zoom change,
        # BEFORE correcting pan -- the gap between this and the cursor's
        # actual (unchanged) position is exactly how far pan needs to
        # move to put it back under the cursor.
        new_rect = self._image_rect()
        now_x = new_rect.left() + rel_x * new_rect.width()
        now_y = new_rect.top() + rel_y * new_rect.height()
        self._pan += QPointF(cursor.x() - now_x, cursor.y() - now_y)
        self.update()
        self.zoom_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size() != event.oldSize():
            self.resized.emit()

    # ---- geometry -----------------------------------------------------

    def _reference_array_size(self):
        """(w, h) of whichever array _image_rect should size itself off
        -- "after" if there is one (its pre-stack source has the same
        dimensions as the graded result, since the GPU/CPU stack chain
        never resizes an image), else "before". None if neither is set."""
        reference = self._after_state.array if self._after_state.array is not None else self._before_state.array
        if reference is None:
            return None
        h, w = reference.shape[:2]
        if w == 0 or h == 0:
            return None
        return w, h

    def _image_rect(self):
        """Centered, aspect-preserved rect both sides are drawn into,
        sized off whichever array is available. self._zoom (1.0 = fit,
        matching the original always-fit behavior exactly) multiplies
        the base fit scale; self._pan then offsets the centered position
        by a widget-space pixel amount -- see wheelEvent/mousePressEvent
        for how each actually changes. When zoomed in, this legitimately
        returns a rect LARGER than the widget's own -- the GPU compositor
        (see gpu_lanczos_compositor.WipeCompositor) only ever renders the
        portion that actually falls within the widget, same as any other
        image viewer's zoomed-in view."""
        size = self._reference_array_size()
        if size is None:
            return None
        img_w, img_h = size
        widget_rect = QRectF(self.rect())
        fit_scale = min(widget_rect.width() / img_w, widget_rect.height() / img_h)
        scale = fit_scale * self._zoom
        draw_w, draw_h = img_w * scale, img_h * scale
        x = (widget_rect.width() - draw_w) / 2 + self._pan.x()
        y = (widget_rect.height() - draw_h) / 2 + self._pan.y()
        return QRectF(x, y, draw_w, draw_h)

    # ---- GPU rendering --------------------------------------------------

    def initializeGL(self):
        self._gpu_stack_renderer = gpu_stack_renderer.GpuStackRenderer()
        self._wipe_compositor = gpu_lanczos_compositor.WipeCompositor()

    def paintGL(self):
        """Thin wrapper around _paint_gl_impl -- catches and logs ANY
        exception rather than letting it propagate back into PySide/
        Shiboken's own virtual-method-override error handling.

        This is NOT just defensive style: a real, reproduced, otherwise
        UNDIAGNOSABLE hard crash (SIGSEGV, confirmed via macOS crash
        reports across three separate occurrences, always the identical
        stack: Shiboken::Errors::storePythonOverrideErrorOrPrint ->
        PepException_GetArgs, called from QOpenGLWidgetWrapper::paintGL())
        traces back to whatever exception-reporting path PySide/Shiboken
        itself uses when a Python override of a C++ virtual method (like
        this one) raises -- that path itself is crashing, natively,
        before any traceback ever reaches this app's own log, even with
        unbuffered stdout. Catching here, ourselves, first, means our own
        exception ever reaches Shiboken's fragile printer at all -- and
        finally gives this bug's actual Python traceback a place to land
        instead of a silent segfault."""
        try:
            self._paint_gl_impl()
        except Exception:
            import sys
            import traceback
            print("WipeCompareWidget.paintGL: exception caught (see traceback below) -- "
                  "this would otherwise crash PySide's own exception-reporting path, see "
                  "this method's own docstring", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    def _paint_gl_impl(self):
        # See __init__'s own comment on _constructed -- a real, reproduced
        # hazard, not theoretical: Qt can deliver a paint callback into
        # this widget before/around its own __init__ finishing.
        if not getattr(self, "_constructed", False):
            return
        # Same reparent-mid-teardown hazard lut_viewer_3d.py's
        # _SafeGLViewWidget.paintGL() guards against (see this module's
        # own docstring) -- now with real GL calls below, this guard is
        # load-bearing, not just free insurance the way it was in Stage 1.
        if not self.isValid():
            return

        import OpenGL.GL as gl
        bg = self.palette().color(QPalette.ColorRole.Base)
        background_color = (bg.redF(), bg.greenF(), bg.blueF())
        placeholder_color = (PLACEHOLDER_BG.redF(), PLACEHOLDER_BG.greenF(), PLACEHOLDER_BG.blueF())

        image_rect = self._image_rect()
        if image_rect is None:
            gl.glClearColor(*background_color, 1.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            return

        before_texture, before_size = self._before_state.ensure_texture(self._gpu_stack_renderer)
        after_texture, after_size = self._after_state.ensure_texture(self._gpu_stack_renderer)

        widget_w = max(1, self.width())
        widget_h = max(1, self.height())
        self._wipe_compositor.render(
            before_texture=before_texture, before_size=before_size,
            after_texture=after_texture, after_size=after_size,
            widget_w=widget_w, widget_h=widget_h,
            image_rect_qt=(image_rect.left(), image_rect.top(), image_rect.width(), image_rect.height()),
            wipe_enabled=self._wipe_enabled, divider_fraction=self._wipe_fraction,
            background_color=background_color, placeholder_color=placeholder_color)

    def paintEvent(self, event):
        """Runs the real GL rendering (via Qt's own internal FBO
        compositing, calling our paintGL above), then draws the wipe
        handle/divider line and color-readout chip with QPainter ON TOP
        -- Qt's documented way to mix a custom paintGL with QPainter
        overlay content in the same QOpenGLWidget: by the time
        super().paintEvent(event) returns, the GL-rendered frame is
        already composited into this widget's backing store, so a
        perfectly normal QPainter(self) session afterward draws over it
        exactly like it would over any other already-painted content --
        no beginNativePainting/endNativePainting brackets needed, since
        nothing here interleaves raw GL calls WITHIN a single QPainter
        session, paintGL and this overlay are two entirely separate
        painting passes run back to back."""
        if not getattr(self, "_constructed", False):
            return
        super().paintEvent(event)
        if not self.isValid():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        image_rect = self._image_rect()
        if image_rect is None:
            painter.setPen(EMPTY_TEXT_COLOR)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                              "Import an image and load a LUT to preview")
            painter.end()
            return

        if self._after_state.array is None:
            # paintGL already filled image_rect with the placeholder
            # color via WipeCompositor's uPlaceholderColor -- draw the
            # explanatory text on top of it here (GL doesn't do text).
            painter.setPen(EMPTY_TEXT_COLOR)
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter,
                              self._after_placeholder_text)

        if not self._wipe_enabled:
            painter.end()
            return

        divider_x = image_rect.left() + image_rect.width() * self._wipe_fraction
        painter.setPen(QPen(HANDLE_COLOR, HANDLE_WIDTH))
        painter.drawLine(QPointF(divider_x, image_rect.top()),
                          QPointF(divider_x, image_rect.bottom()))

        center_y = image_rect.top() + image_rect.height() / 2
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        painter.setBrush(HANDLE_COLOR)
        painter.drawEllipse(QPointF(divider_x, center_y), HANDLE_GRIP_RADIUS, HANDLE_GRIP_RADIUS)

        # No BEFORE/AFTER corner labels anymore (per Tom) -- with Left/
        # Right each independently pickable from three sources (Sample
        # Before, Sample After, or Reference -- see sample_image_panel.py),
        # a fixed "BEFORE"/"AFTER" tag would be wrong as often as not (e.g.
        # Reference on the left, or Sample Before on the right), and
        # labeling each side correctly would mean this widget needing to
        # know the source keys/display names sample_image_panel.py owns.
        # Simpler to just drop the overlay entirely rather than plumb that
        # through -- the "..." menu's Left/Right submenus already show
        # which source is checked if you need a reminder.

        painter.end()

    # ---- mouse interaction ----------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            # Panning (per Tom) -- middle button specifically, so it can
            # never conflict with the left-button wipe-divider drag right
            # below: both can be reached from the exact same cursor
            # position without either one stealing the other's gesture.
            self._panning = True
            self._pan_start_mouse = event.position()
            self._pan_start_offset = QPointF(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if not self._wipe_enabled:
            return
        image_rect = self._image_rect()
        if image_rect is not None and image_rect.contains(event.position()):
            self._dragging = True
            self._update_wipe_from_x(event.position().x(), image_rect)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.position() - self._pan_start_mouse
            self._pan = self._pan_start_offset + delta
            self.update()
            return
        if not self._wipe_enabled:
            return
        image_rect = self._image_rect()
        if image_rect is None:
            return
        if self._dragging:
            self._update_wipe_from_x(event.position().x(), image_rect)
        divider_x = image_rect.left() + image_rect.width() * self._wipe_fraction
        near_handle = abs(event.position().x() - divider_x) < HANDLE_GRIP_RADIUS + 4
        self.setCursor(
            Qt.CursorShape.SizeHorCursor if (near_handle or self._dragging)
            else Qt.CursorShape.ArrowCursor
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_start_mouse = None
            self._pan_start_offset = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        self._dragging = False

    def _update_wipe_from_x(self, x, image_rect):
        if image_rect.width() <= 0:
            return
        fraction = (x - image_rect.left()) / image_rect.width()
        self.set_wipe_fraction(fraction)
