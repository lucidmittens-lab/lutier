"""
timeline_track_widget.py -- TimelineTrackWidget: a proper NLE-style
timeline track, clips drawn as time-proportional blocks along a
horizontal x axis, per Tom: "start laying the groundwork for a more
robust timeline viewer. Something more akin to a normal NLE. Clips in
the edit should be represented as blocks on a timeline where the x axis
represents time." Replaces timeline_panel.py's old plain-icon QListWidget
rows (a uniform-height list with no sense of relative duration at all)
as that panel's primary view -- see that module's own docstring for the
integration.

Same "Qt-free math as plain functions + thin QWidget paint/drag layer"
split every other interactive control in this app uses (see
color_wheel_widget.py/curve_editor_widget.py's own docstrings) -- the
module-level functions below (frame_to_x/x_to_frame/clip_extents/
clip_at_x/reorder_target_index/ruler_tick_frames) are independently
testable without constructing a widget at all, and are the ONLY place
that understands the pixel<->frame mapping; the widget class is a thin
paint/mouse-event layer on top of them.

TIME AXIS IS RECORD-SPACE, ALWAYS -- every clip's on-screen x position
comes from its position in the WHOLE Timeline's own frame numbering
(clip_extents' running total, the exact same "sum every earlier clip's
frame_count" computation TimelineController.current_record_frame/
_locate_record_frame already do independently for playback -- kept as a
SEPARATE small implementation here rather than importing that
controller, since this widget has no controller dependency at all, only
a timeline.Timeline; see this module's own test file for a cross-check
that the two independently-implemented "record position" computations
agree).

ZOOM/SCROLL (per Tom, 2026-10-03: "make it more similar to a typical
NLE") -- pixels_per_frame is either auto-fit (the original, still-default
behavior: recomputed from the widget's own current width whenever the
Timeline's total_frame_count or the widget's size changes, so the whole
Timeline fits in view) or a manually-set zoom level (self._zoom_pixels_
per_frame, set via set_zoom_pixels_per_frame/zoom_in/zoom_out and cleared
back to auto-fit via zoom_to_fit) -- see refresh()'s own docstring.
Zoomed in, self._scroll_offset_frames (a record-space frame offset, not
pixels, so it stays meaningful across a zoom change) shifts what's
visible; _origin_x() is the ONE place that folds BOTH the track's left
margin and the current scroll offset into a pixel origin, and every
frame_to_x/x_to_frame call in this module goes through it rather than
track.left() directly, so scrolling doesn't need its own parallel set of
hit-testing/painting code paths. TimelinePanel owns the actual zoom
in/out/fit buttons and a QScrollBar wired to this widget's own
visible_frame_count/max_scroll_frames/set_scroll_offset_frames -- this
widget only exposes the mechanism, not the chrome. Ctrl+wheel zooms
(keeping the frame under the cursor stationary); a plain wheel scrolls.

SELECTION mirrors timeline_panel.TimelinePanel.clip_selected's own shape
exactly (Signal(object), a TimelineClip or None) so that panel's
existing external contract (and everything main_window.py already wires
to it) doesn't need to change at all when this widget replaces its old
list-based view internally.

REORDER-BY-DRAG reuses timeline.Timeline.move_clip directly (the exact
same data-layer method the old list's InternalMove drag already funneled
into via _resync_clip_order_from_list_widget) -- this widget only ever
decides WHICH index to move to (reorder_target_index, a pure function),
never touches list order itself beyond that one call.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same situation as color_
wheel_widget.py/curve_editor_widget.py's own docstrings: the underlying
math and real QWidget/QPainter/QMouseEvent behavior are exercised
headlessly, but whether it actually READS well and feels right to drag
needs a real display.
"""
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

import media_cache
import timecode as tc_module

MIN_PIXELS_PER_FRAME = 0.02  # a multi-hour Timeline still gets SOME width per frame, never truly zero
MAX_PIXELS_PER_FRAME = 200.0  # zooming in further than individual frames being huge blocks isn't useful
ZOOM_STEP_FACTOR = 1.25  # each zoom_in()/zoom_out()/wheel-notch step, see those methods' own docstrings
RULER_HEIGHT = 20
TRACK_MARGIN = 6
MIN_TICK_SPACING_PX = 56  # ruler ticks never crowd closer than this
THUMBNAIL_LABEL_MIN_WIDTH = 40  # blocks narrower than this show no text, just color/thumbnail
THUMBNAIL_MIN_WIDTH = 14  # blocks narrower than this show no thumbnail either, just a flat block
THUMBNAIL_ICON_WIDTH = 34  # a small fixed-size icon at the block's left edge, not stretched across it
EDGE_GRAB_PX = 6  # how close to a clip's own boundary a click/hover counts as grabbing its trim handle

_NICE_TICK_SECONDS = (
    1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400,
)


def clip_extents(clips):
    """[(clip, start_frame, end_frame_exclusive), ...] -- start_frame is
    this clip's own position in the WHOLE Timeline's record-space
    numbering (every earlier clip's own frame_count, summed), matching
    TimelineController.current_record_frame's identical computation
    (kept independent on purpose, see module docstring). end_frame_
    exclusive - start_frame == clip.frame_count."""
    extents = []
    running = 0
    for clip in clips:
        extents.append((clip, running, running + clip.frame_count))
        running += clip.frame_count
    return extents


def pixels_per_frame_to_fit(total_frame_count, available_width):
    """The zoom level (pixels per record-space frame) that makes the
    WHOLE Timeline exactly fill `available_width` -- see module
    docstring on why this is the only zoom mode for now. A Timeline with
    no clips/no frames, or a widget not laid out yet (available_width <=
    0), returns MIN_PIXELS_PER_FRAME rather than dividing by zero."""
    if total_frame_count <= 0 or available_width <= 0:
        return MIN_PIXELS_PER_FRAME
    return max(MIN_PIXELS_PER_FRAME, available_width / total_frame_count)


def frame_to_x(frame, pixels_per_frame, origin_x=0):
    return origin_x + frame * pixels_per_frame


def x_to_frame(x, pixels_per_frame, origin_x=0):
    if pixels_per_frame <= 0:
        return 0
    return (x - origin_x) / pixels_per_frame


def clip_at_x(clips, x, pixels_per_frame, origin_x=0):
    """The TimelineClip whose block spans pixel position `x`, or None if
    `x` falls in the gap past the last clip (or the Timeline is empty).
    Uses clip_extents' own start/end pair converted through frame_to_x,
    so this always agrees with wherever paintEvent actually drew each
    block."""
    frame = x_to_frame(x, pixels_per_frame, origin_x)
    for clip, start, end in clip_extents(clips):
        if start <= frame < end:
            return clip
    return None


def clip_edge_at_x(clips, x, pixels_per_frame, origin_x=0, grab_px=EDGE_GRAB_PX):
    """(clip, "left"|"right") if pixel position `x` is within `grab_px`
    of that clip's own boundary, else None -- the hit-test behind drag-
    to-trim (per Tom: "make it more similar to a typical NLE"). The grab
    zone is in PIXELS, not frames, so it stays a constant, grabbable
    width regardless of zoom. A block narrower than 2*grab_px skips edge
    hit-testing entirely (its own left/right zones would overlap and
    there's no room to trim it precisely anyway) -- clicking it hits its
    BODY instead (see clip_at_x), same as before this existed."""
    for clip, start, end in clip_extents(clips):
        left_x = frame_to_x(start, pixels_per_frame, origin_x)
        right_x = frame_to_x(end, pixels_per_frame, origin_x)
        if right_x - left_x <= 2 * grab_px:
            continue
        if abs(x - left_x) <= grab_px:
            return clip, "left"
        if abs(x - right_x) <= grab_px:
            return clip, "right"
    return None


def apply_left_trim(clip, start, frame):
    """The clamped new in_frame for dragging `clip`'s LEFT edge so its
    own record-space start lands at `frame` (e.g. from x_to_frame) --
    `start` is this clip's own CURRENT record-space start (from clip_
    extents, computed fresh by the caller on every drag step, since
    preceding clips' own frame_counts -- the only thing that could move
    `start` -- never change during a single clip's own trim). Clamped to
    [0, clip.out_frame - 1] so the clip never inverts or vanishes (always
    at least 1 frame left)."""
    offset = clip.in_frame - start
    return max(0, min(round(frame) + offset, clip.out_frame - 1))


def apply_right_trim(clip, end, frame):
    """Same as apply_left_trim, for the RIGHT edge -- `end` is this
    clip's own CURRENT record-space EXCLUSIVE end (clip_extents' own
    convention) and `frame` is the NEW exclusive end the cursor landed
    on (both in the same units frame_to_x/x_to_frame use for a clip's
    own end -- one past its last frame), so both get the same "-1"
    treatment converting an exclusive end into its own last frame's
    record-space position before the fixed start<->source offset is
    applied. Clamped to >= clip.in_frame (at least 1 frame)."""
    offset = clip.out_frame - (end - 1)
    return max(clip.in_frame, round(frame) - 1 + offset)


def reorder_target_index(clips, dragged_clip_id, x, pixels_per_frame, origin_x=0):
    """Which index `dragged_clip_id` should move to, given the OTHER
    clips' current on-screen positions and where the drag currently is
    (x, the dragged block's own center) -- counts how many OTHER clips'
    own center points sit to the left of x, which is exactly "how many
    clips this one now belongs after." Ignores the dragged clip's own
    current position entirely (it's excluded from the count), so this
    is stable regardless of where the drag started. Returns an index
    directly usable with timeline.Timeline.move_clip."""
    frame = x_to_frame(x, pixels_per_frame, origin_x)
    target = 0
    for clip, start, end in clip_extents(clips):
        if clip.id == dragged_clip_id:
            continue
        center = (start + end) / 2
        if frame >= center:
            target += 1
    return target


def ruler_tick_frames(total_frame_count, frame_rate, pixels_per_frame):
    """Frame indices to draw a ruler tick (+ timecode label) at -- picks
    the smallest "nice" interval (1/2/5/10/15/30s, then nice minute/hour
    steps) whose on-screen spacing is still >= MIN_TICK_SPACING_PX, so
    ticks never crowd together regardless of zoom, and always land on a
    human-readable round-seconds boundary rather than an arbitrary frame
    count. Always includes frame 0. Empty for a frame_rate/
    pixels_per_frame that can't produce a positive interval (defensive
    only -- callers already guard the Timeline-has-no-clips case)."""
    if frame_rate <= 0 or pixels_per_frame <= 0 or total_frame_count <= 0:
        return []
    seconds_per_tick = None
    for candidate in _NICE_TICK_SECONDS:
        if candidate * frame_rate * pixels_per_frame >= MIN_TICK_SPACING_PX:
            seconds_per_tick = candidate
            break
    if seconds_per_tick is None:
        # Even the widest nice interval above is still too tight (an
        # extremely long Timeline zoomed very far out) -- fall back to
        # a plain round-number-of-ticks-across-the-total split instead
        # of leaving the ruler blank.
        seconds_per_tick = _NICE_TICK_SECONDS[-1]
    frame_step = max(1, round(seconds_per_tick * frame_rate))
    return list(range(0, total_frame_count, frame_step))


class TimelineTrackWidget(QWidget):
    """`timeline`: a timeline.Timeline, set via set_timeline (or the
    constructor). Auto-fits to the widget's own current width by default
    -- see pixels_per_frame_to_fit's own docstring and this module's own
    docstring on the zoom/scroll mechanism."""

    clip_selected = Signal(object)          # a timeline.TimelineClip, or None
    clip_double_clicked = Signal(object)    # a timeline.TimelineClip -- Rename shortcut, see timeline_panel.py
    clips_reordered = Signal()              # fired after a drag actually changes clip order
    clip_trimmed = Signal()                 # fired after a drag-to-trim actually changes a clip's in/out
    delete_requested = Signal()             # Delete/Backspace pressed with a clip selected, see keyPressEvent
    seek_requested = Signal(int)            # a record-space frame index, from clicking/dragging the ruler/empty track
    context_menu_requested_for_clip = Signal(object, QPoint)  # (TimelineClip, global QPoint) -- right-click
    view_changed = Signal()                 # zoom level and/or scroll position changed -- see module docstring

    def __init__(self, timeline=None, parent=None):
        super().__init__(parent)
        self.timeline = timeline
        self.selected_clip_id = None
        self.playhead_frame = None
        self._pixels_per_frame = MIN_PIXELS_PER_FRAME
        self._zoom_pixels_per_frame = None  # None == auto-fit, see module docstring
        self._scroll_offset_frames = 0.0
        self._drag_clip_id = None
        self._drag_target_index = None
        self._scrubbing = False
        self._trim_clip_id = None
        self._trim_edge = None  # "left" or "right" while self._trim_clip_id is set
        self.setMinimumHeight(RULER_HEIGHT + 64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Needed for hover-only mouseMoveEvent calls (no button held) --
        # that's how the trim-handle resize cursor (see _update_hover_
        # cursor) tracks the mouse without a drag in progress.
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Delete/Backspace needs real keyboard focus, see keyPressEvent
        # customContextMenuRequested, not a contextMenuEvent override --
        # same right-click convention every other panel's list/table in
        # this app already uses (media_panel.py/timeline_panel.py's old
        # list, etc.), and the only one confirmed to actually fire from
        # a real right-click under this Qt build/offscreen platform.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_custom_context_menu_requested)

    def set_timeline(self, timeline):
        self.timeline = timeline
        self.refresh()

    def refresh(self):
        """Recomputes pixels_per_frame (auto-fit, or the current manual
        zoom level -- see set_zoom_pixels_per_frame/zoom_to_fit), re-
        clamps the scroll offset against the new content size, and
        repaints. Call after anything changes the Timeline's own clip
        list/frame_rate, or this widget's selection/playhead."""
        total = self.timeline.total_frame_count() if self.timeline else 0
        if self._zoom_pixels_per_frame is None:
            self._pixels_per_frame = pixels_per_frame_to_fit(total, self._track_width())
        else:
            self._pixels_per_frame = self._zoom_pixels_per_frame
        self._clamp_scroll()
        self.update()
        self.view_changed.emit()

    def set_playhead_frame(self, frame):
        if frame == self.playhead_frame:
            return
        self.playhead_frame = frame
        self.update()

    def _track_width(self):
        return max(0, self.width() - 2 * TRACK_MARGIN)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    # ---- zoom / scroll ------------------------------------------------------

    def visible_frame_count(self):
        """How many record-space frames currently fit across the track's
        own width at the current zoom -- TimelinePanel uses this (and
        max_scroll_frames) to size its scrollbar's page step/range."""
        return self._track_width() / self._pixels_per_frame if self._pixels_per_frame > 0 else 0.0

    def max_scroll_frames(self):
        total = self.timeline.total_frame_count() if self.timeline else 0
        return max(0.0, total - self.visible_frame_count())

    def scroll_offset_frames(self):
        return self._scroll_offset_frames

    def _clamp_scroll(self):
        self._scroll_offset_frames = max(0.0, min(self._scroll_offset_frames, self.max_scroll_frames()))

    def set_scroll_offset_frames(self, frames):
        clamped = max(0.0, min(frames, self.max_scroll_frames()))
        if clamped != self._scroll_offset_frames:
            self._scroll_offset_frames = clamped
            self.update()
            self.view_changed.emit()

    def set_zoom_pixels_per_frame(self, pixels_per_frame, anchor_x=None):
        """Switches to a manual zoom level -- `anchor_x` (a local pixel
        x, e.g. the cursor position), if given, keeps whatever frame was
        under that pixel BEFORE the zoom change still under it
        afterward (the usual "zoom to cursor" feel); omitted, the left
        edge of the current view stays put instead."""
        old_ppf = self._pixels_per_frame
        anchor_frame = x_to_frame(anchor_x, old_ppf, self._origin_x()) if anchor_x is not None else None
        self._zoom_pixels_per_frame = max(MIN_PIXELS_PER_FRAME, min(pixels_per_frame, MAX_PIXELS_PER_FRAME))
        self._pixels_per_frame = self._zoom_pixels_per_frame
        if anchor_frame is not None:
            track = self._track_rect()
            target_origin_x = anchor_x - anchor_frame * self._pixels_per_frame
            self._scroll_offset_frames = (track.left() - target_origin_x) / self._pixels_per_frame
        self._clamp_scroll()
        self.update()
        self.view_changed.emit()

    def zoom_in(self, anchor_x=None):
        self.set_zoom_pixels_per_frame(self._pixels_per_frame * ZOOM_STEP_FACTOR, anchor_x)

    def zoom_out(self, anchor_x=None):
        self.set_zoom_pixels_per_frame(self._pixels_per_frame / ZOOM_STEP_FACTOR, anchor_x)

    def zoom_to_fit(self):
        """Back to auto-fit mode (see refresh's own docstring) -- the
        original, still-default behavior."""
        self._zoom_pixels_per_frame = None
        self._scroll_offset_frames = 0.0
        self.refresh()

    def is_zoomed_to_fit(self):
        return self._zoom_pixels_per_frame is None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+wheel zooms, keeping the frame under the cursor still
            # under it afterward -- same convention most NLE timelines
            # already use for this gesture.
            if delta > 0:
                self.zoom_in(anchor_x=event.position().x())
            else:
                self.zoom_out(anchor_x=event.position().x())
        else:
            # Plain wheel -- horizontal scroll. NOT VISUALLY VERIFIED
            # (module docstring) -- the exact direction/feel may want a
            # tweak once seen on a real display/trackpad.
            frames_per_notch = max(1.0, self.visible_frame_count() * 0.1)
            direction = -1 if delta > 0 else 1
            self.set_scroll_offset_frames(self._scroll_offset_frames + direction * frames_per_notch)
        event.accept()

    # ---- hit testing / geometry -------------------------------------------

    def _clips(self):
        return self.timeline.clips if self.timeline else []

    def _track_rect(self):
        return QRect(TRACK_MARGIN, RULER_HEIGHT, self._track_width(), max(0, self.height() - RULER_HEIGHT))

    def _origin_x(self):
        """The pixel x that record-space frame 0 maps to -- folds the
        track's own left margin together with the current scroll offset
        (module docstring) into the one value every frame_to_x/x_to_frame
        call in this class should use as origin_x, instead of track.
        left() directly."""
        return self._track_rect().left() - self._scroll_offset_frames * self._pixels_per_frame

    def _clip_rect(self, start_frame, end_frame):
        track = self._track_rect()
        origin_x = self._origin_x()
        x0 = frame_to_x(start_frame, self._pixels_per_frame, origin_x)
        x1 = frame_to_x(end_frame, self._pixels_per_frame, origin_x)
        return QRectF(x0, track.top(), max(1.0, x1 - x0), track.height())

    # ---- mouse interaction --------------------------------------------------

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        origin_x = self._origin_x()
        # The ruler strip is always a scrub target, regardless of what's
        # under it in the track row below -- under pure auto-fit-to-width
        # zoom, clips can span the entire track width, leaving no "empty"
        # track-row pixel to click for a seek at all.
        edge_hit = None
        clip = None
        if pos.y() >= RULER_HEIGHT:
            edge_hit = clip_edge_at_x(self._clips(), pos.x(), self._pixels_per_frame, origin_x)
            if edge_hit is None:
                clip = clip_at_x(self._clips(), pos.x(), self._pixels_per_frame, origin_x)
        if edge_hit is not None:
            clip, edge = edge_hit
            self._set_selected(clip)
            self._trim_clip_id = clip.id
            self._trim_edge = edge
            event.accept()
            return
        if clip is not None:
            self._set_selected(clip)
            self._drag_clip_id = clip.id
            self._drag_target_index = None
            event.accept()
            return
        # Empty track/ruler space -- a scrub, same "click jumps straight
        # to the cursor" convention video_scrub_slider.py's own
        # mousePressEvent already established for the single-clip case.
        # Held down (see mouseMoveEvent), this becomes a genuine drag-to-
        # scrub across the whole Timeline, not just a single-point jump.
        self._scrubbing = True
        frame = round(x_to_frame(pos.x(), self._pixels_per_frame, origin_x))
        self.seek_requested.emit(max(0, frame))
        event.accept()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            # No button held -- a hover, not a drag (setMouseTracking(True)
            # is what makes this fire at all). Only ever updates the trim-
            # handle resize cursor, see module docstring.
            self._update_hover_cursor(event.position())
            return
        origin_x = self._origin_x()
        if self._trim_clip_id is not None:
            self._apply_trim_drag(event.position().x(), origin_x)
            return
        if self._scrubbing:
            frame = round(x_to_frame(event.position().x(), self._pixels_per_frame, origin_x))
            self.seek_requested.emit(max(0, frame))
            return
        if self._drag_clip_id is None:
            return
        self._drag_target_index = reorder_target_index(
            self._clips(), self._drag_clip_id, event.position().x(), self._pixels_per_frame, origin_x)
        self.update()

    def _update_hover_cursor(self, pos):
        origin_x = self._origin_x()
        if pos.y() >= RULER_HEIGHT and clip_edge_at_x(self._clips(), pos.x(), self._pixels_per_frame, origin_x):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()

    def _apply_trim_drag(self, x, origin_x):
        """Drag-to-trim (per Tom: "make it more similar to a typical
        NLE") -- a RIPPLE trim: since clips sit strictly end-to-end with
        no gap concept (timeline.py's own model), shortening/lengthening
        one clip automatically shifts every later clip's own record-
        space position, with no separate ripple step needed."""
        clip = next((c for c in self._clips() if c.id == self._trim_clip_id), None)
        if clip is None:
            return
        frame = x_to_frame(x, self._pixels_per_frame, origin_x)
        for candidate, start, end in clip_extents(self._clips()):
            if candidate.id != self._trim_clip_id:
                continue
            if self._trim_edge == "left":
                new_in = apply_left_trim(clip, start, frame)
                if new_in != clip.in_frame:
                    clip.in_frame = new_in
                    self.refresh()
                    self.clip_trimmed.emit()
            else:
                new_out = apply_right_trim(clip, end, frame)
                if new_out != clip.out_frame:
                    clip.out_frame = new_out
                    self.refresh()
                    self.clip_trimmed.emit()
            return

    def mouseReleaseEvent(self, event):
        if self._trim_clip_id is not None:
            self._trim_clip_id = None
            self._trim_edge = None
            event.accept()
            return
        if self._scrubbing:
            self._scrubbing = False
            event.accept()
            return
        if self._drag_clip_id is None:
            return
        clip_id, target_index = self._drag_clip_id, self._drag_target_index
        self._drag_clip_id = None
        self._drag_target_index = None
        self.update()
        if target_index is None or self.timeline is None:
            return
        current_index = next((i for i, c in enumerate(self.timeline.clips) if c.id == clip_id), None)
        if current_index is None or current_index == target_index:
            return
        self.timeline.move_clip(clip_id, target_index)
        self.refresh()
        self.clips_reordered.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        clip = clip_at_x(self._clips(), event.position().x(), self._pixels_per_frame, self._origin_x())
        if clip is not None:
            self.clip_double_clicked.emit(clip)

    def keyPressEvent(self, event):
        # Delete/Backspace removes the selected clip -- same focus-
        # scoped convention as stack_panel.py's _StackListWidget (only
        # fires while THIS widget actually has keyboard focus, see
        # mousePressEvent's own setFocus call and __init__'s
        # setFocusPolicy), not a window-wide QShortcut.
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_clip_id is not None:
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def _on_custom_context_menu_requested(self, pos):
        clip = clip_at_x(self._clips(), pos.x(), self._pixels_per_frame, self._origin_x())
        if clip is None:
            return
        self._set_selected(clip)
        self.context_menu_requested_for_clip.emit(clip, self.mapToGlobal(pos))

    def _set_selected(self, clip):
        new_id = clip.id if clip is not None else None
        if new_id == self.selected_clip_id:
            return
        self.selected_clip_id = new_id
        self.update()
        self.clip_selected.emit(clip)

    def selected_clip(self):
        if self.selected_clip_id is None:
            return None
        for clip in self._clips():
            if clip.id == self.selected_clip_id:
                return clip
        return None

    def set_selected_clip_id(self, clip_id):
        """Programmatic selection (e.g. main_window.py syncing FROM
        another source of truth) -- does NOT re-emit clip_selected,
        same "external sync shouldn't re-trigger the signal that
        started it" convention as this app's other panels' load_*
        methods."""
        if clip_id == self.selected_clip_id:
            return
        self.selected_clip_id = clip_id
        self.update()

    # ---- painting -----------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#141414"))  # _DARK_BASE, see style.py

        clips = self._clips()
        track = self._track_rect()
        self._paint_ruler(painter, track)
        if not clips:
            self._paint_empty_hint(painter, track)
            painter.end()
            return

        for clip, start, end in clip_extents(clips):
            display_start, display_end = start, end
            if self._drag_clip_id == clip.id and self._drag_target_index is not None:
                # Ghost-follows-the-target-slot feedback while dragging --
                # the dragged block itself still paints at its OWN
                # frame position (simplest correct feedback: everything
                # else stays put, only a highlighted insertion marker
                # moves -- see _paint_insertion_marker below), so no
                # position override is needed here after all; kept as a
                # no-op branch so a future "ghost block follows the
                # cursor" enhancement has an obvious place to live.
                pass
            self._paint_clip(painter, clip, display_start, display_end)

        if self._drag_clip_id is not None and self._drag_target_index is not None:
            self._paint_insertion_marker(painter, track, clips)

        if self.playhead_frame is not None:
            self._paint_playhead(painter, track)
        painter.end()

    def _paint_ruler(self, painter, track):
        total = self.timeline.total_frame_count() if self.timeline else 0
        frame_rate = self.timeline.frame_rate if self.timeline else 0
        painter.fillRect(QRect(0, 0, self.width(), RULER_HEIGHT), QColor("#1a1a1c"))  # _DARK_DARK
        painter.setPen(QColor("#4d4d53"))  # _DARK_MID
        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        origin_x = self._origin_x()
        for frame in ruler_tick_frames(total, frame_rate, self._pixels_per_frame):
            x = frame_to_x(frame, self._pixels_per_frame, origin_x)
            painter.drawLine(int(x), RULER_HEIGHT - 5, int(x), RULER_HEIGHT)
            label = tc_module.frames_to_timecode(frame, frame_rate) if frame_rate else str(frame)
            painter.drawText(int(x) + 2, RULER_HEIGHT - 6, label)

    def _paint_empty_hint(self, painter, track):
        painter.setPen(QColor("#BDBDBD"))  # _HINT_TEXT
        painter.drawText(track, Qt.AlignmentFlag.AlignCenter, "No clips -- Add to Timeline / Add to Sequence")

    def _paint_clip(self, painter, clip, start_frame, end_frame):
        rect = self._clip_rect(start_frame, end_frame)
        selected = clip.id == self.selected_clip_id
        being_dragged = clip.id == self._drag_clip_id

        painter.fillRect(rect, QColor("#38383c"))  # _DARK_BUTTON
        png_bytes = media_cache.load_thumbnail(clip.thumbnail) if clip.thumbnail else None
        if png_bytes and rect.width() >= THUMBNAIL_MIN_WIDTH:
            pixmap = QPixmap()
            if pixmap.loadFromData(png_bytes, "PNG"):
                # A small fixed-size icon pinned to the block's left edge
                # -- stretching the thumbnail to cover the WHOLE block
                # (as this used to do) reads badly once a clip is more
                # than a couple hundred frames wide, and isn't needed to
                # tell clips apart at a glance.
                icon_width = min(THUMBNAIL_ICON_WIDTH, rect.width())
                icon_rect = QRectF(rect.left(), rect.top(), icon_width, rect.height())
                painter.save()
                painter.setClipRect(icon_rect)
                scaled = pixmap.scaled(
                    icon_rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                painter.drawPixmap(
                    int(icon_rect.left() + (icon_rect.width() - scaled.width()) / 2),
                    int(icon_rect.top() + (icon_rect.height() - scaled.height()) / 2),
                    scaled)
                painter.restore()

        border_color = self.palette().highlight().color() if selected else QColor("#4d4d53")  # _DARK_MID
        pen = painter.pen()
        pen.setColor(border_color)
        pen.setWidth(2 if selected else 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        if being_dragged:
            painter.fillRect(rect, QColor(255, 255, 255, 40))

        if rect.width() >= THUMBNAIL_LABEL_MIN_WIDTH:
            label_rect = QRectF(rect.left(), rect.bottom() - 16, rect.width(), 16)
            painter.fillRect(label_rect, QColor(0, 0, 0, 170))
            painter.setPen(QColor("#e6e6e9"))  # _TEXT_PRIMARY
            font = QFont()
            font.setPixelSize(9)
            font.setBold(True)
            painter.setFont(font)
            kind_glyph = "🎬" if clip.media_kind == "video" else "🖼"
            text = f"{kind_glyph} {clip.name}"
            painter.drawText(
                label_rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(label_rect.width() - 8)))

    def _paint_insertion_marker(self, painter, track, clips):
        origin_x = self._origin_x()
        extents = [(c, s, e) for c, s, e in clip_extents(clips) if c.id != self._drag_clip_id]
        if self._drag_target_index >= len(extents):
            x = frame_to_x(
                extents[-1][2] if extents else 0, self._pixels_per_frame, origin_x)
        else:
            x = frame_to_x(extents[self._drag_target_index][1], self._pixels_per_frame, origin_x)
        pen = painter.pen()
        pen.setColor(self.palette().highlight().color())
        pen.setWidth(3)
        painter.setPen(pen)
        painter.drawLine(int(x), track.top(), int(x), track.bottom())

    def _paint_playhead(self, painter, track):
        x = frame_to_x(self.playhead_frame, self._pixels_per_frame, self._origin_x())
        pen = painter.pen()
        pen.setColor(QColor(255, 255, 255, 235))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(int(x), 0, int(x), self.height())
