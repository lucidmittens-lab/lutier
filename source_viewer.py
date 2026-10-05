"""
source_viewer.py -- the Source viewer (Tom, 2026-10-05: "we must have
both source and record windows"). Shows the clip loaded from Media
Libraries UNGRADED -- grading is judged on Record (the existing Viewer,
sample_image_panel.py) -- with its own transport, In/Out marks, and the
three edits into the loaded timeline: Insert (F9), Overwrite (F10) and
Append (Shift+F12), at Record's playhead.

Transport (transport_logic.py): J/K/L shuttle (L/J repeatedly for 2x,
4x, 8x; K stops; K held + J/L steps a frame), Space play/pause, Left/
Right one frame, Shift+Left/Right one second, Up/Down to the start/end,
I/O mark in/out, Alt+X clear marks, Q/W go to in/out. The timecode at the
top right is typed into (Enter) -- full, partial or +/- relative TC.

ClipPlayer plays forward through a FrameServer (decoded ahead on a
worker thread, like Record) and reverse by seeking per frame on its own
clock -- cheap for the all-intra camera codecs (ProRes, DNx) this is
mostly used with. Parked frames are decoded at PARK_MAX_DIMENSION.
"""
import time

from PySide6.QtCore import QObject, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QFontDatabase, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

import app_logic
import timecode
import transport_icons
import transport_logic
import video_source
from frame_server import FrameServer, Segment
from video_playback_controller import DECODE_PROXY_MAX_DIMENSION
from video_scrub_slider import VideoScrubSlider
from wipe_compare_widget import WipeCompareWidget

PARK_MAX_DIMENSION = 2048
PLACEHOLDER = "Double-click a clip in Media Libraries to view it here"


class ClipPlayer(QObject):
    """Decodes one clip for the Source viewer: parked seeks, forward
    playback via a FrameServer, reverse playback by seeking. Emits
    frame_ready(array, frame_index) for every frame to show."""

    frame_ready = Signal(object, int)
    playing_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.kind = None
        self.frame_count = 0
        self.frame_rate = 0.0
        self.frame_index = 0
        self.speed = 0
        self._source = None
        self._server = FrameServer(DECODE_PROXY_MAX_DIMENSION)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self._clock_start = None
        self._clock_origin = 0

    def load(self, path, kind, frame_index=0):
        """Open `path` ("video" or "still") and show `frame_index`.
        Raises if the file can't be read."""
        self.stop()
        self.close()
        if kind == "video":
            self._source = video_source.VideoSource(path)
            self.frame_count = max(1, self._source.frame_count)
            self.frame_rate = self._source.frame_rate or 24.0
        self.path, self.kind = path, kind
        if kind != "video":
            self.frame_count, self.frame_rate = 1, 0.0
            self.frame_ready.emit(app_logic.load_image_as_array(path, max_dimension=PARK_MAX_DIMENSION), 0)
            self.frame_index = 0
            return
        self.seek(frame_index)

    def close(self):
        self._server.stop()
        if self._source is not None:
            self._source.close()
            self._source = None
        self.path = self.kind = None
        self.frame_count, self.frame_rate, self.frame_index = 0, 0.0, 0

    @property
    def is_playing(self):
        return self.speed != 0

    def seek(self, frame_index, max_dimension=PARK_MAX_DIMENSION):
        if self._source is None:
            return
        frame_index = max(0, min(int(frame_index), self.frame_count - 1))
        frame = self._source.seek_to_frame(frame_index, max_dimension=max_dimension)
        if frame is not None:
            self.frame_index = frame_index
            self.frame_ready.emit(frame, frame_index)

    def play(self, speed):
        """Signed speed: 1 is realtime forward, -2 double-speed reverse..."""
        if self._source is None or speed == 0:
            self.stop()
            return
        was_playing = self.is_playing
        self.speed = speed
        self._clock_start = time.perf_counter()
        self._clock_origin = self.frame_index
        if speed > 0:
            step = max(1, round(speed))
            self._server.restart([Segment(self.path, "video", 0, self.frame_count)], 0,
                                 self.frame_index + step, step=step)
        else:
            self._server.stop()
        self._timer.start(max(1, round(1000.0 / self.frame_rate)))
        if not was_playing:
            self.playing_changed.emit(True)

    def stop(self):
        """Stop playing and re-park on the current frame at full preview size."""
        if not self.is_playing:
            return
        self._timer.stop()
        self._server.stop()
        self.speed = 0
        self.seek(self.frame_index)
        self.playing_changed.emit(False)

    def _on_timer(self):
        elapsed = time.perf_counter() - self._clock_start
        target = self._clock_origin + int(elapsed * self.frame_rate * self.speed)
        if target >= self.frame_count or target < 0:
            self.frame_index = max(0, min(target, self.frame_count - 1))
            self.stop()
            return
        if target == self.frame_index:
            return
        if self.speed > 0:
            latest = self._server.take_latest_up_to(0, target)
            if latest is None:
                return  # nothing decoded yet -- hold; the clock keeps running
            index, frame = latest
        else:
            frame = self._source.seek_to_frame(target, max_dimension=DECODE_PROXY_MAX_DIMENSION)
            index = target
            if frame is None:
                return
        self.frame_index = index
        self.frame_ready.emit(frame, index)


class _ElidedLabel(QLabel):
    """A QLabel that shortens its text with "..." to fit, instead of
    clipping it or forcing the panel wider."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full_text = text or ""
        self._refresh()

    def text(self):
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        elided = QFontMetrics(self.font()).elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width()))
        super().setText(elided)


def _tool_button(icon_name, tooltip, slot):
    button = QToolButton()
    button.setObjectName("videoTransportButton")
    button.setAutoRaise(True)
    button.setIcon(transport_icons.icon(icon_name))
    button.setIconSize(QSize(18, 18))
    button.setToolTip(tooltip)
    button.clicked.connect(slot)
    return button


class SourceViewerPanel(QWidget):
    # (mode, clip spec) -- mode "insert" / "overwrite" / "append"; the spec
    # is a dict: name, path, kind, in_frame, out_frame, thumbnail. MainWindow
    # performs the edit on the loaded timeline at Record's playhead.
    edit_requested = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._name = ""
        self._thumbnail = None
        self._start_frame = 0      # absolute frame of the clip's embedded start TC
        self.in_point = None
        self.out_point = None
        self._shuttle = transport_logic.Shuttle()
        self._k_held = False
        self._suppress_scrub = False
        self.player = ClipPlayer(self)
        self.player.frame_ready.connect(self._on_frame_ready)
        self.player.playing_changed.connect(self._on_playing_changed)
        self._build_layout()
        self._build_shortcuts()
        self._refresh_controls()

    # ---- layout -------------------------------------------------------------

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        self.name_label = _ElidedLabel(PLACEHOLDER)
        header.addWidget(self.name_label, stretch=3)
        # Marks readout in the header (the transport row is full of
        # buttons in a narrow dock). Never widens the panel -- a long
        # readout here once raised the Source dock's minimum width and
        # squeezed the other columns; full text is in the tooltip.
        self.marks_label = _ElidedLabel()
        self.marks_label.setObjectName("viewerMarks")
        self.marks_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.marks_label, stretch=4)
        header.addSpacing(8)
        self.timecode_edit = QLineEdit()
        self.timecode_edit.setObjectName("viewerTimecode")
        self.timecode_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.timecode_edit.setToolTip(
            "Type a timecode and press Enter: a full TC, a partial one (replaces the "
            "right-hand digits, e.g. 1000 = 10s 00f), or +/- an offset (+200 = +2s)")
        self.timecode_edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.timecode_edit.setFixedWidth(QFontMetrics(self.timecode_edit.font()).horizontalAdvance("00:00:00:00") + 26)
        self.timecode_edit.returnPressed.connect(self._on_timecode_entered)
        header.addWidget(self.timecode_edit)
        layout.addLayout(header)

        self.display = WipeCompareWidget()
        self.display.set_wipe_enabled(False)
        self.display.set_after_placeholder(PLACEHOLDER)
        self.display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.display, stretch=1)

        self.scrub = VideoScrubSlider(Qt.Orientation.Horizontal)
        self.scrub.setMinimum(0)
        self.scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scrub.valueChanged.connect(self._on_scrub_changed)
        layout.addWidget(self.scrub)

        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(2)
        self.go_to_in_button = _tool_button("go_to_in", "Go to In (Q)", self.go_to_in)
        self.step_back_button = _tool_button("step_back", "Back one frame (Left)", lambda: self.step(-1))
        self.reverse_button = _tool_button("play_reverse", "Play reverse (J)", lambda: self._shuttle_key("j"))
        self.play_button = _tool_button("play", "Play / Pause (Space)", self.toggle_play)
        self.step_forward_button = _tool_button("step_forward", "Forward one frame (Right)", lambda: self.step(1))
        self.go_to_out_button = _tool_button("go_to_out", "Go to Out (W)", self.go_to_out)
        for button in (self.go_to_in_button, self.step_back_button, self.reverse_button,
                       self.play_button, self.step_forward_button, self.go_to_out_button):
            row.addWidget(button)
        row.addSpacing(10)
        self.mark_in_button = _tool_button("mark_in", "Mark In (I)", self.mark_in)
        self.mark_out_button = _tool_button("mark_out", "Mark Out (O)", self.mark_out)
        self.clear_marks_button = _tool_button("clear_marks", "Clear In/Out (Alt+X)", self.clear_marks)
        for button in (self.mark_in_button, self.mark_out_button, self.clear_marks_button):
            row.addWidget(button)
        row.addStretch(1)
        self.insert_button = _tool_button("insert", "Insert at the Record playhead (F9)",
                                          lambda: self.request_edit("insert"))
        self.overwrite_button = _tool_button("overwrite", "Overwrite at the Record playhead (F10)",
                                             lambda: self.request_edit("overwrite"))
        self.append_button = _tool_button("append", "Append to the end of the timeline (Shift+F12)",
                                          lambda: self.request_edit("append"))
        for button in (self.insert_button, self.overwrite_button, self.append_button):
            row.addWidget(button)
        layout.addLayout(row)

    def _build_shortcuts(self):
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        bindings = (
            ("Space", self.toggle_play), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
            ("Shift+Left", lambda: self.step(-self._fps())), ("Shift+Right", lambda: self.step(self._fps())),
            ("Up", lambda: self.seek(0)), ("Down", lambda: self.seek(self.player.frame_count - 1)),
            ("I", self.mark_in), ("O", self.mark_out), ("Alt+X", self.clear_marks),
            ("Q", self.go_to_in), ("W", self.go_to_out),
            ("F9", lambda: self.request_edit("insert")), ("F10", lambda: self.request_edit("overwrite")),
            ("Shift+F12", lambda: self.request_edit("append")),
        )
        self._shortcuts = []
        for keys, slot in bindings:
            shortcut = QShortcut(QKeySequence(keys), self, context=context)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    # J/K/L need key-up for K (hold K + J/L = step), so they're plain key events.
    def keyPressEvent(self, event):
        key = event.key()
        if event.isAutoRepeat() and key in (Qt.Key.Key_J, Qt.Key.Key_L, Qt.Key.Key_K):
            return
        if key == Qt.Key.Key_K:
            self._k_held = True
            self._shuttle_key("k")
        elif key == Qt.Key.Key_J:
            self._shuttle_key("j")
        elif key == Qt.Key.Key_L:
            self._shuttle_key("l")
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_K and not event.isAutoRepeat():
            self._k_held = False
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    # ---- loading --------------------------------------------------------------

    def load_entry(self, entry, frame_index=0):
        """Open a Media Library entry (media_library.MediaEntry)."""
        tc = entry.metadata.get("start_timecode") if entry.metadata else None
        start = timecode.timecode_to_frames(tc, entry.frame_rate or 0) if tc else None
        self.load_path(entry.source_path, entry.media_kind, entry.name,
                       frame_index=frame_index, start_frame=start or 0, thumbnail=entry.thumbnail)

    def load_path(self, path, kind, name, frame_index=0, start_frame=0, thumbnail=None):
        try:
            self.player.load(path, kind, frame_index=frame_index)
        except Exception as e:  # noqa: BLE001 -- shown in place, never a dialog mid-edit
            self.display.set_after(None)
            self.display.set_after_placeholder(f"Couldn't open {name}: {e}")
            return False
        self._name, self._thumbnail, self._start_frame = name, thumbnail, start_frame
        self.in_point = self.out_point = None
        self._suppress_scrub = True
        self.scrub.setMaximum(max(0, self.player.frame_count - 1))
        self.scrub.setValue(self.player.frame_index)
        self._suppress_scrub = False
        self._refresh_controls()
        return True

    def clear(self):
        self.player.close()
        self._name = ""
        self.in_point = self.out_point = None
        self.display.set_after(None)
        self.display.set_after_placeholder(PLACEHOLDER)
        self._refresh_controls()

    # ---- transport --------------------------------------------------------------

    def _fps(self):
        return max(1, round(self.player.frame_rate or 24))

    def _loaded_video(self):
        return self.player.kind == "video"

    def seek(self, frame_index):
        if not self._loaded_video():
            return
        self.player.stop()
        self._shuttle.press_k()
        self.player.seek(frame_index)

    def step(self, frames):
        self.seek(self.player.frame_index + frames)

    def toggle_play(self):
        if self.player.is_playing:
            self._shuttle_key("k")
        else:
            self._shuttle_key("l")

    def _shuttle_key(self, key):
        if not self._loaded_video():
            return
        if key == "k":
            self._shuttle.press_k()
            self.player.stop()
            return
        press = self._shuttle.press_l if key == "l" else self._shuttle.press_j
        action, value = press(k_held=self._k_held)
        if action == "step":
            self.step(value)
        else:
            self.player.play(value)

    def go_to_in(self):
        if self.in_point is not None:
            self.seek(self.in_point)

    def go_to_out(self):
        if self.out_point is not None:
            self.seek(self.out_point)

    def mark_in(self):
        if not self.player.path:
            return
        self.in_point = self.player.frame_index
        if self.out_point is not None and self.out_point < self.in_point:
            self.out_point = None
        self._refresh_controls()

    def mark_out(self):
        if not self.player.path:
            return
        self.out_point = self.player.frame_index
        if self.in_point is not None and self.in_point > self.out_point:
            self.in_point = None
        self._refresh_controls()

    def clear_marks(self):
        self.in_point = self.out_point = None
        self._refresh_controls()

    def marked_range(self):
        """(in_frame, out_frame) to edit with: the marks, defaulting to the
        whole clip on either side that isn't marked."""
        last = max(0, self.player.frame_count - 1)
        start = self.in_point if self.in_point is not None else 0
        end = self.out_point if self.out_point is not None else last
        return start, max(start, end)

    def request_edit(self, mode):
        if not self.player.path:
            return
        start, end = self.marked_range()
        self.edit_requested.emit(mode, {
            "name": self._name, "path": self.player.path, "kind": self.player.kind,
            "in_frame": start, "out_frame": end, "thumbnail": self._thumbnail,
        })

    def _on_timecode_entered(self):
        rate = self.player.frame_rate
        target = transport_logic.parse_timecode_entry(
            self.timecode_edit.text(), self.player.frame_index, rate, self._start_frame)
        if target is None:
            self._refresh_timecode()
            return
        self.seek(target)
        self.setFocus()

    def _on_scrub_changed(self, value):
        if not self._suppress_scrub:
            self.seek(value)

    # ---- display ---------------------------------------------------------------

    def _on_frame_ready(self, array, frame_index):
        self.display.set_after(array, gpu_stack=None)
        self._suppress_scrub = True
        self.scrub.setValue(frame_index)
        self._suppress_scrub = False
        self._refresh_timecode()

    def _on_playing_changed(self, playing):
        self.play_button.setIcon(transport_icons.icon("pause" if playing else "play"))

    def _tc(self, frame):
        return timecode.frames_to_timecode(self._start_frame + frame, self.player.frame_rate) \
            if self.player.frame_rate else ""

    def _refresh_timecode(self):
        if not self.timecode_edit.hasFocus():
            self.timecode_edit.setText(self._tc(self.player.frame_index) if self._loaded_video() else "")

    def _refresh_controls(self):
        loaded = bool(self.player.path)
        video = self._loaded_video()
        self.name_label.setText(self._name if loaded else PLACEHOLDER)
        self.name_label.setToolTip(self.player.path or "")
        for widget in (self.go_to_in_button, self.step_back_button, self.reverse_button, self.play_button,
                       self.step_forward_button, self.go_to_out_button, self.mark_in_button,
                       self.mark_out_button, self.clear_marks_button, self.scrub, self.timecode_edit):
            widget.setEnabled(video)
        for widget in (self.insert_button, self.overwrite_button, self.append_button):
            widget.setEnabled(loaded)
        self.scrub.set_in_out(self.in_point, self.out_point)
        if video and (self.in_point is not None or self.out_point is not None):
            start, end = self.marked_range()
            text = (f"IN {self._tc(start)}  OUT {self._tc(end)}  "
                    f"DUR {timecode.frames_to_timecode(end - start + 1, self.player.frame_rate)}")
            self.marks_label.setText(text)
            self.marks_label.setToolTip(text)
        else:
            self.marks_label.setText("")
        self._refresh_timecode()
