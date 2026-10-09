"""sample_image_panel/transport.py -- the Record viewer's transport bar:
timecode fields, play/step/scrub, J/K/L shuttle and keyboard shortcuts,
in/out marks, loop, speed and Add to Sequence. A mixin: SampleImagePanel
inherits it, so these methods share the panel's own `self`.
"""
from pathlib import Path
import time
from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QFontDatabase, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout,
    QWidget, QSizePolicy, QLabel,
)
from video_scrub_slider import VideoScrubSlider
import video_playback_controller
import transport_icons
import transport_logic
import timecode
from .constants import SCRUB_SETTLE_DEBOUNCE_MS, _PAUSE_GLYPH, _PLAY_GLYPH


class _TimecodeField(QLineEdit):
    """A timecode readout you can type into (Enter jumps): looks like a
    label until focused; selects everything on focus so typing replaces."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoTimecodeLabel")
        self.setFrame(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Fixed, not just capped: a timecode must never be squeezed to "22".
        # Measured in the monospace font it's drawn in (style.py).
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance("00:00:00:00") + 22)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)


def _fix_size_across_texts(button, texts):
    """Pin `button` to the largest size it takes for any of `texts` and
    leave it showing texts[0] -- for a button whose label toggles (Play/
    Pause), so the toggle can never resize it or the row it sits in."""
    width = height = 0
    for text in texts:
        button.setText(text)
        hint = button.sizeHint()
        width, height = max(width, hint.width()), max(height, hint.height())
    button.setFixedSize(width, height)
    button.setText(texts[0])


class TransportMixin:
    def _active_transport_controller(self):
        """Whichever controller the transport bar's Play/Pause/Loop/
        Speed act on right now -- self._playback_controller (SOURCE, the
        single directly-loaded/selected clip) or self._timeline_
        controller (RECORD, the Timeline's own composited playback),
        picked by the SOURCE/RECORD toggle button, not anything
        automatic. Both expose the identical is_playing/play/pause/
        toggle/set_loop_enabled/set_speed/playback_started/
        playback_stopped surface on purpose, so every transport handler
        below can stay a single call through this method rather than an
        if/else per action."""
        return self._playback_controller if self._viewer_mode == "source" else self._timeline_controller

    def _build_video_transport_bar(self, layout):
        """Play/Pause, step-back/step-forward, in/out points + loop,
        speed, and a scrub slider -- a thin bar below the Viewer, visible
        while a video is loaded OR the Timeline has clips (see
        refresh_video_transport_visibility, load_video/_close_video_
        source, and main_window.py's own TimelinePanel.timeline_changed
        wiring). Deliberately colocated here in sample_image_panel.py
        rather than a new panel file -- same "own the state, own the UI
        that changes it" shape every other control in this panel already
        follows.

        TRANSPORT / SCRUB SPLIT -- originally (Tom, 2026-08-24) "the
        transport can be exclusive to the timeline BUT there should be a
        scrub bar only that's exclusive to the clip," later widened by
        the SOURCE/RECORD toggle (see _active_transport_controller):
        Play/Pause/Loop/Speed now drive WHICHEVER of self._playback_
        controller (SOURCE)/self._timeline_controller (RECORD) the
        toggle button is currently set to, not always the Timeline.
        Step/Set In/Set Out/Clear In-Out are still disabled outright
        (see _on_video_step_back_clicked's own docstring on why, and
        what's deferred -- that part of the original split is
        unchanged). The scrub slider below always drives self.
        _playback_controller/self._video_source regardless of the
        current toggle state, same as before the toggle existed.

        Keyboard shortcuts (Space/Left/Right/I/O/Home/End) are wired
        separately in _build_video_keyboard_shortcuts, called once from
        __init__ (not per-transport-bar-build, since shortcuts attach to
        `self`, not this bar's own widgets) -- Left/Right/I/O/Home/End
        are effectively inert now too, routed to the same disabled
        handlers as their matching buttons."""
        self._video_transport_widget = QWidget()
        outer_layout = QVBoxLayout(self._video_transport_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(2)

        # Scrub bar gets its OWN row, full width (per direct feedback:
        # squeezed inline next to a row of fixed-size buttons/combos, it
        # was "vanishingly small next to the other controls" -- it was
        # the only stretch=1 widget sharing space with everything else's
        # fixed minimum width). Buttons/loop/speed/timecode all move to
        # a second row below instead.
        scrub_row_layout = QHBoxLayout()
        scrub_row_layout.setContentsMargins(0, 0, 0, 0)
        self._video_scrub_slider = VideoScrubSlider(Qt.Orientation.Horizontal)
        self._video_scrub_slider.setMinimum(0)
        self._video_scrub_slider.valueChanged.connect(self._on_video_scrub_slider_changed)
        scrub_row_layout.addWidget(self._video_scrub_slider, stretch=1)
        outer_layout.addLayout(scrub_row_layout)

        transport_layout = QHBoxLayout()
        transport_layout.setContentsMargins(0, 0, 0, 0)
        transport_layout.setSpacing(2)  # within a group -- see addSpacing(8) BETWEEN groups below
        outer_layout.addLayout(transport_layout)

        self._video_play_button = QToolButton()
        self._video_play_button.setObjectName("videoTransportButton")
        # Fixed to the larger of its two states: a glyph the UI font lacks
        # falls back to whatever font has it (on Linux "⏸" came from STIX
        # Two Math, whose tall line height grew the whole transport bar
        # the moment Play was pressed).
        # Icon first, so the fixed size below accounts for it.
        self._video_play_button.setIcon(transport_icons.icon("play"))
        self._video_play_button.setIconSize(QSize(18, 18))
        _fix_size_across_texts(self._video_play_button, (_PLAY_GLYPH, _PAUSE_GLYPH))
        self._video_play_button.setToolTip("Play/Pause (Space)")
        self._video_play_button.clicked.connect(self._on_video_play_pause_clicked)
        transport_layout.addWidget(self._video_play_button)

        # Step is still disabled (Tom, 2026-08-24: the transport is
        # exclusive to the Timeline) -- see _on_video_step_back_clicked's
        # own docstring on why frame-stepping specifically can't just be
        # left pointed at the single-clip controller (it would silently
        # hijack the Viewer away from whatever's currently on screen in
        # RECORD mode). Left in place, greyed out, rather than removed
        # outright -- a real Timeline-aware version is a scoped
        # fast-follow, not a dead feature. Set In/Set Out/Clear below
        # were disabled here too for the same original reason, but
        # re-enabled per Tom's later "marking in and out dont seem to
        # work at all" -- unlike Step, they always act on the single
        # directly-loaded/selected clip's own _playback_controller
        # regardless of the current SOURCE/RECORD toggle (see their own
        # handlers' docstrings), so there's no "which controller do I
        # hijack" ambiguity for them the way there is for Step.
        self._video_step_back_button = QToolButton()
        self._video_step_back_button.setObjectName("videoTransportButton")
        self._video_step_back_button.setText("◀│")
        self._video_step_back_button.setIcon(transport_icons.icon('step_back'))
        self._video_step_back_button.setIconSize(QSize(18, 18))
        self._video_step_back_button.setToolTip("Not yet supported for Timeline playback")
        self._video_step_back_button.setEnabled(False)
        self._video_step_back_button.clicked.connect(self._on_video_step_back_clicked)
        transport_layout.addWidget(self._video_step_back_button)

        self._video_step_forward_button = QToolButton()
        self._video_step_forward_button.setObjectName("videoTransportButton")
        self._video_step_forward_button.setText("│▶")
        self._video_step_forward_button.setIcon(transport_icons.icon('step_forward'))
        self._video_step_forward_button.setIconSize(QSize(18, 18))
        self._video_step_forward_button.setToolTip("Not yet supported for Timeline playback")
        self._video_step_forward_button.setEnabled(False)
        self._video_step_forward_button.clicked.connect(self._on_video_step_forward_clicked)
        transport_layout.addWidget(self._video_step_forward_button)

        transport_layout.addWidget(self._build_video_transport_separator())
        transport_layout.addSpacing(4)

        self._video_set_in_button = QToolButton()
        self._video_set_in_button.setObjectName("videoTransportButton")
        self._video_set_in_button.setText("[")
        self._video_set_in_button.setIcon(transport_icons.icon('mark_in'))
        self._video_set_in_button.setIconSize(QSize(18, 18))
        self._video_set_in_button.setToolTip("Set In point at the current frame (I)")
        self._video_set_in_button.clicked.connect(self._on_video_set_in_clicked)
        transport_layout.addWidget(self._video_set_in_button)

        self._video_set_out_button = QToolButton()
        self._video_set_out_button.setObjectName("videoTransportButton")
        self._video_set_out_button.setText("]")
        self._video_set_out_button.setIcon(transport_icons.icon('mark_out'))
        self._video_set_out_button.setIconSize(QSize(18, 18))
        self._video_set_out_button.setToolTip("Set Out point at the current frame (O)")
        self._video_set_out_button.clicked.connect(self._on_video_set_out_clicked)
        transport_layout.addWidget(self._video_set_out_button)

        self._video_clear_in_out_button = QToolButton()
        self._video_clear_in_out_button.setObjectName("videoTransportButton")
        self._video_clear_in_out_button.setText("⊘")
        self._video_clear_in_out_button.setIcon(transport_icons.icon('clear_marks'))
        self._video_clear_in_out_button.setIconSize(QSize(18, 18))
        self._video_clear_in_out_button.setToolTip("Clear In/Out (reset to the full clip)")
        self._video_clear_in_out_button.clicked.connect(self._on_video_clear_in_out_clicked)
        transport_layout.addWidget(self._video_clear_in_out_button)

        # Add to Sequence (per Tom: "will be necessary to be able to
        # mark desired ranges and add to sequence") -- appends a NEW
        # Timeline clip covering exactly the marked [In, Out] range of
        # the single directly-loaded/selected clip (the full clip if
        # nothing's been marked -- see get_source_range_for_timeline's
        # own docstring), via add_source_range_to_timeline_requested
        # (wired in main_window.py to TimelinePanel.add_clip_with_range,
        # the same "this panel has no idea TimelinePanel exists"
        # arrangement every other cross-panel signal in this app uses).
        self._video_add_to_sequence_button = QToolButton()
        self._video_add_to_sequence_button.setObjectName("videoTransportButton")
        self._video_add_to_sequence_button.setText("➕")
        self._video_add_to_sequence_button.setIcon(transport_icons.icon('append'))
        self._video_add_to_sequence_button.setIconSize(QSize(18, 18))
        self._video_add_to_sequence_button.setToolTip(
            "Add the marked In/Out range (or the whole clip, if nothing's "
            "marked) to the Sequence as a new Timeline clip")
        self._video_add_to_sequence_button.clicked.connect(self._on_video_add_to_sequence_clicked)
        transport_layout.addWidget(self._video_add_to_sequence_button)

        self._video_loop_button = QToolButton()
        self._video_loop_button.setObjectName("videoTransportButton")
        self._video_loop_button.setText("⟲")
        self._video_loop_button.setIcon(transport_icons.icon('loop'))
        self._video_loop_button.setIconSize(QSize(18, 18))
        self._video_loop_button.setToolTip("Loop playback -- the clip in SOURCE mode, the Timeline in RECORD mode")
        self._video_loop_button.setCheckable(True)
        self._video_loop_button.toggled.connect(self._on_video_loop_toggled)
        transport_layout.addWidget(self._video_loop_button)

        transport_layout.addWidget(self._build_video_transport_separator())
        transport_layout.addSpacing(4)

        self._video_speed_combo = QComboBox()
        for preset in video_playback_controller.SPEED_PRESETS:
            self._video_speed_combo.addItem(f"{preset:g}x", preset)
        self._video_speed_combo.setCurrentIndex(
            video_playback_controller.SPEED_PRESETS.index(video_playback_controller.DEFAULT_SPEED))
        self._video_speed_combo.currentIndexChanged.connect(self._on_video_speed_changed)
        self._video_speed_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        transport_layout.addWidget(self._video_speed_combo)

        # IDENTITY PASS: the free space goes to a quiet readout of how the
        # picture is shown ("Fit 47% · Wipe off"), not to a stretched speed
        # box -- see _update_viewer_readout.
        transport_layout.addStretch(1)
        self._viewer_readout = QLabel("")
        self._viewer_readout.setObjectName("viewerReadout")
        transport_layout.addWidget(self._viewer_readout)

        # Two timecode fields (per direct feedback: "there are 3
        # timecode fields ... you can make it 2 only" -- collapses the
        # single-clip Source readout and the Timeline's own combined
        # "Src ... Rec ..." readout into one unified Source field plus a
        # standing Record field):
        #
        #   - _source_timecode_label: whichever "source" is actually
        #     ACTIVE right now -- the single directly-loaded/selected
        #     clip's own position in SOURCE mode (self._video_source),
        #     or the Timeline's CURRENT clip's own source position in
        #     RECORD mode (TimelineController.current_source_frame --
        #     "the clip under the playhead"), never both at once. See
        #     _update_source_timecode_label, the single place that
        #     decides which.
        #   - _record_timecode_label: the Timeline's own position
        #     (TimelineController.current_record_frame) -- unlike the
        #     Source field, this ALWAYS reflects the whole Timeline
        #     regardless of which mode the toggle is on, since "Record"
        #     is unambiguous (there's only ever one Timeline). Blank
        #     whenever the Timeline has no clips at all.
        #
        # Both expressed at the Timeline's own frame_rate when showing a
        # Timeline-sourced position (see timeline_playback_controller
        # .py's current_source_frame/current_record_frame docstrings on
        # why mixed-fps-aware timecode isn't attempted here -- explicitly
        # out of scope for this pass); the single-clip Source case uses
        # that clip's own frame_rate, same as before this merge.
        # Type-to-jump timecode fields (same entry rules as the Source
        # viewer, transport_logic.parse_timecode_entry) -- QLineEdits that
        # look like labels until clicked; text()/setText() as before.
        self._source_timecode_label = _TimecodeField()
        self._source_timecode_label.returnPressed.connect(self._on_source_timecode_entered)
        self._source_timecode_label.setToolTip(
            "Position within whichever source is currently active: the "
            "single loaded/selected clip (SOURCE mode) or the Timeline's "
            "current clip under the playhead (RECORD mode)")
        transport_layout.addSpacing(4)
        transport_layout.addWidget(self._source_timecode_label)

        self._record_timecode_label = _TimecodeField()
        self._record_timecode_label.setToolTip(
            "Position within the whole Timeline -- type a timecode and press Enter to jump")
        self._record_timecode_label.returnPressed.connect(self._on_record_timecode_entered)
        transport_layout.addSpacing(8)
        transport_layout.addWidget(self._record_timecode_label)

        self._video_transport_widget.setVisible(False)  # shown once a video is loaded OR the Timeline has clips
        layout.addWidget(self._video_transport_widget)

    @staticmethod
    def _build_video_transport_separator():
        """A thin vertical divider between logical control groups (Phase
        4 follow-up, per Tom: transport bar felt like "one long row" --
        groups it into play/step, in/out/loop, scrub, and speed/timecode
        clusters instead)."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        return separator

    def _build_video_keyboard_shortcuts(self):
        """Space/Left/Right/I/O/Home/End (Phase 4 transport upgrade, per
        Tom's own confirmed scope) -- attached to `self` (this whole
        panel) with the default WidgetWithChildrenShortcut context, so
        they fire whenever focus is anywhere inside this panel (the
        Viewer/transport bar), not globally across the app and not only
        when one specific sub-widget has focus. All are permanently
        active (not conditionally enabled only while a video is loaded)
        -- each handler already routes through methods that are no-ops
        with no video open (_playback_controller.toggle/pause,
        seek_video_frame), so there's nothing to gate here."""
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._on_video_play_pause_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._on_video_step_back_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._on_video_step_forward_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self._on_video_set_in_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self._on_video_set_out_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, self._on_video_jump_to_in_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_End), self, self._on_video_jump_to_out_clicked)
        # J/K/L shuttle -- plain key events (K must be HELD for K+J/L frame
        # steps), see keyPressEvent/_shuttle_key.
        self._shuttle = transport_logic.Shuttle()
        self._k_held = False
        self._reverse_timer = QTimer(self)
        self._reverse_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._reverse_timer.timeout.connect(self._on_reverse_tick)
        self._reverse_clock = None  # (perf_counter start, start position, speed)

    def _shuttle_key(self, key):
        """L forward (2x/4x/8x on repeats), J reverse, K stop; K held + J/L
        steps one frame -- on whichever mode (CLIP/TIMELINE) is active."""
        controller = self._active_transport_controller()
        if key == "k":
            self._shuttle.press_k()
            self._stop_reverse()
            controller.pause()
            self._update_video_play_button_text()
            return
        press = self._shuttle.press_l if key == "l" else self._shuttle.press_j
        action, value = press(k_held=self._k_held)
        if action == "step":
            self._stop_reverse()
            controller.pause()
            (self._on_video_step_forward_clicked if value > 0 else self._on_video_step_back_clicked)()
        elif value > 0:
            self._stop_reverse()
            controller.set_speed(float(value))
            if not controller.is_playing:
                controller.play()
        else:
            controller.pause()
            self._start_reverse(-value)
        self._update_video_play_button_text()

    def _current_position(self):
        """(position, frame_rate) in the active mode: the clip frame (CLIP)
        or the record frame (TIMELINE); (None, 0) if there's nothing."""
        if self._viewer_mode == "record":
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            position = self._timeline_controller.current_record_frame()
            return position, (timeline_obj.frame_rate if timeline_obj is not None else 0)
        if self._video_source is None:
            return None, 0
        return self._video_frame_index, self._video_source.frame_rate

    def _seek_active(self, position, downres=False):
        if self._viewer_mode == "record":
            self._timeline_controller.seek_to_record_frame(max(0, position), downres=downres)
        elif self._video_source is not None:
            last = max(0, (self._video_source.frame_count or 1) - 1)
            self.seek_video_frame(max(0, min(position, last)), downres=downres)

    def _start_reverse(self, speed):
        position, rate = self._current_position()
        if position is None or not rate:
            return
        self._reverse_clock = (time.perf_counter(), position, speed, rate)
        self._reverse_timer.start(max(1, round(1000.0 / rate)))

    def _stop_reverse(self):
        self._reverse_timer.stop()
        self._reverse_clock = None

    def _on_reverse_tick(self):
        """Reverse shuttle: seek backwards on the clock (frames due since it
        started), at the scrub resolution -- drops frames rather than
        falling behind; stops at the first frame."""
        if self._reverse_clock is None:
            return
        started, origin, speed, rate = self._reverse_clock
        target = origin - int((time.perf_counter() - started) * rate * speed)
        if target <= 0:
            self._seek_active(0)
            self._shuttle.press_k()
            self._stop_reverse()
            return
        self._seek_active(target, downres=True)

    def _on_source_timecode_entered(self):
        """Typed into the Source TC field: a position in the current clip."""
        self.setFocus()
        if self._viewer_mode == "record":
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            source_frame = self._timeline_controller.current_source_frame()
            record = self._timeline_controller.current_record_frame()
            if timeline_obj is None or source_frame is None or record is None:
                return
            target = transport_logic.parse_timecode_entry(
                self._source_timecode_label.text(), source_frame, timeline_obj.frame_rate)
            if target is not None:
                self._seek_active(record + (target - source_frame))
            return
        position, rate = self._current_position()
        if position is None:
            return
        target = transport_logic.parse_timecode_entry(self._source_timecode_label.text(), position, rate)
        if target is not None:
            self._seek_active(target)

    def _on_record_timecode_entered(self):
        """Typed into the Record TC field: a position in the whole timeline."""
        self.setFocus()
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        record = self._timeline_controller.current_record_frame()
        if timeline_obj is None or not timeline_obj.clips:
            return
        target = transport_logic.parse_timecode_entry(
            self._record_timecode_label.text(), record or 0, timeline_obj.frame_rate)
        if target is not None:
            self.activate_record_mode()
            self._seek_active(target)

    def _set_video_transport_position(self, frame_index):
        """Updates the scrub slider + timecode label to reflect
        `frame_index` WITHOUT re-triggering a seek -- called after every
        successful parked-frame load (_load_video_frame_core). Scrub bar
        write gated to SOURCE mode, same reasoning as _on_video_frame_
        changed/_on_timeline_frame_changed's own gates -- a parked
        single-clip seek (e.g. session restore) while RECORD mode is
        active must not stomp the Timeline-range scrub bar."""
        if self._viewer_mode == "source":
            self._suppress_video_scrub_signal = True
            self._video_scrub_slider.setValue(frame_index)
            self._suppress_video_scrub_signal = False
        self._update_video_timecode_label(frame_index)

    _SOURCE_TIMECODE_TOOLTIP = (
        "Position within whichever source is currently active: the "
        "single loaded/selected clip (SOURCE mode) or the Timeline's "
        "current clip under the playhead (RECORD mode)")

    def _update_video_timecode_label(self, frame_index):
        """Real SMPTE timecode for the single directly-loaded/selected
        clip (Phase 4, replacing the old "N / total" label -- see
        timecode.py) -- frame count is kept as a secondary detail in the
        tooltip, not the primary display, per the plan's own scope note.

        Only actually writes to the shared _source_timecode_label while
        SOURCE mode is active (see that label's own construction-time
        docstring) -- RECORD mode's Source field comes from _on_
        timeline_frame_changed instead, and a background tick from the
        single-clip controller while it's the INACTIVE one must not
        overwrite that."""
        if self._viewer_mode != "source":
            return
        if self._video_source is None:
            self._source_timecode_label.setText("")
            self._source_timecode_label.setToolTip(self._SOURCE_TIMECODE_TOOLTIP)
            return
        tc = timecode.frames_to_timecode(frame_index, self._video_source.frame_rate)
        self._source_timecode_label.setText(tc)
        self._source_timecode_label.setToolTip(
            f"Frame {frame_index + 1} / {self._video_source.frame_count}")

    def _on_video_play_pause_clicked(self):
        # Nothing loaded to play in CLIP mode but a timeline with clips:
        # play the timeline (the mode follows what there is to play).
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if (self._viewer_mode == "source" and self._video_source is None
                and timeline_obj is not None and timeline_obj.clips):
            self.activate_record_mode()
        self._active_transport_controller().toggle()
        self._update_video_play_button_text()

    def _update_video_play_button_text(self):
        playing = self._active_transport_controller().is_playing
        self._video_play_button.setText(_PAUSE_GLYPH if playing else _PLAY_GLYPH)
        self._video_play_button.setIcon(transport_icons.icon("pause" if playing else "play"))

    def _on_video_step_back_clicked(self):
        """Disabled for now (button greyed out, see _build_video_transport
        _bar) -- frame-stepping is scoped to the single directly-loaded
        clip's OWN seek_video_frame, which would silently hijack the
        Viewer away from whatever the Timeline is currently showing (see
        module docstring on the transport/scrub split). A real Timeline-
        aware step (including backward across a clip boundary) is a
        fast-follow, not built in this pass -- see the approved Timeline
        plan's own scope note."""
        return

    def _on_video_step_forward_clicked(self):
        """Disabled for now -- see _on_video_step_back_clicked's own
        docstring."""
        return

    def _on_video_scrub_slider_changed(self, value):
        """Mode-aware (per direct feedback: "the scrub bar seems to only
        represent a single clip in the timeline. it should represent
        the whole timeline") -- SOURCE seeks the single directly-loaded
        clip (unchanged from before the SOURCE/RECORD toggle existed);
        RECORD seeks the whole Timeline via TimelineController.
        seek_to_record_frame, which pauses it first the same way
        _playback_controller.pause() does here for the SOURCE case.

        SCRUB-TIME DOWNRES (see SCRUB_DOWNRES_FACTOR) -- _scrub_settle_
        timer.isActive() being True means a PREVIOUS call to this method
        started it and it hasn't fired yet, i.e. this is at least the
        second tick of an ongoing drag, not an isolated click-to-jump;
        only that case decodes downres'd. Restarting the timer on every
        call (regardless of which branch) is what turns "quiet for
        SCRUB_SETTLE_DEBOUNCE_MS" into the signal that dragging has
        stopped -- see _on_scrub_settled."""
        if self._suppress_video_scrub_signal:
            return
        scrub_in_progress = self._scrub_settle_timer.isActive()
        self._scrub_settle_timer.start(SCRUB_SETTLE_DEBOUNCE_MS)
        self._last_scrub_frame_index = value
        self._last_scrub_was_downres = scrub_in_progress
        if self._viewer_mode == "record":
            self._timeline_controller.seek_to_record_frame(value, downres=scrub_in_progress)
        else:
            self._playback_controller.pause()
            self.seek_video_frame(value, downres=scrub_in_progress)

    def _on_scrub_settled(self):
        """Fires once a burst of scrub ticks has gone quiet for SCRUB_
        SETTLE_DEBOUNCE_MS. If the most recent tick was downres'd,
        replace it with one final normal-quality decode at the same
        frame now that dragging has stopped -- a no-op if it wasn't (an
        isolated click, or nothing scrubbed yet this session)."""
        if not self._last_scrub_was_downres or self._last_scrub_frame_index is None:
            return
        frame_index = self._last_scrub_frame_index
        self._last_scrub_was_downres = False
        if self._viewer_mode == "record":
            self._timeline_controller.seek_to_record_frame(frame_index, downres=False)
        else:
            self.seek_video_frame(frame_index, downres=False)

    def _sync_video_in_out_markers(self):
        """Pushes _playback_controller's own in_point/out_point onto the
        scrub slider's visual markers (Phase 4 follow-up, per Tom: "hard
        to tell where the marks are") -- called after every action that
        can change either bound, so the slider never shows stale marks.
        Only ever called for SOURCE mode's own range (see
        _refresh_scrub_slider_for_mode) -- RECORD mode has no in/out
        sub-range concept yet, so it clears the markers instead of
        calling this."""
        self._video_scrub_slider.set_in_out(
            self._playback_controller.in_point, self._playback_controller.out_point)

    def _on_video_set_in_clicked(self):
        """Marks In at the single directly-loaded/selected clip's
        CURRENT frame (per Tom: "marking in and out dont seem to work
        at all") -- always acts on _playback_controller regardless of
        the current SOURCE/RECORD toggle (see this method's sibling
        handlers and the "..." menu's own reasoning), since a marked
        range only ever exists to be added to the Sequence via Add to
        Sequence (_on_video_add_to_sequence_clicked), a single-clip
        concept. Timeline-level in/out (a partial-range TRIM of a clip
        ALREADY on the Timeline) is a different, still-unbuilt feature
        -- see timeline_playback_controller.py's own module docstring."""
        self._playback_controller.set_in_point()
        self._on_video_in_out_changed()

    def _on_video_set_out_clicked(self):
        """See _on_video_set_in_clicked's own docstring."""
        self._playback_controller.set_out_point()
        self._on_video_in_out_changed()

    def _on_video_clear_in_out_clicked(self):
        """See _on_video_set_in_clicked's own docstring."""
        self._playback_controller.clear_in_out()
        self._on_video_in_out_changed()

    def _on_video_in_out_changed(self):
        """Shared tail for Set In/Set Out/Clear -- only actually repaints
        the scrub bar's markers while SOURCE mode is active (see
        _sync_video_in_out_markers/_refresh_scrub_slider_for_mode's own
        docstrings on why RECORD mode's scrub bar has no in/out marker
        concept at all); _playback_controller's own in_point/out_point
        are updated either way, regardless of mode, so a mark made while
        parked in RECORD mode is still there once you switch back."""
        if self._viewer_mode == "source":
            self._sync_video_in_out_markers()

    def _on_video_loop_toggled(self, checked):
        self._active_transport_controller().set_loop_enabled(checked)

    def _on_video_speed_changed(self, index):
        speed = self._video_speed_combo.itemData(index)
        if speed is not None:
            self._active_transport_controller().set_speed(speed)

    def _on_video_jump_to_in_clicked(self):
        """Home -- jumps to the SAME in_point Set In/Out controls (see
        _on_video_set_in_clicked). Pauses first, same "an explicit jump
        always parks, never leaves the timer ticking through it"
        convention the scrub bar's own drag-to-seek already uses."""
        if self._video_source is None:
            return
        self._playback_controller.pause()
        self.seek_video_frame(self._playback_controller.in_point)

    def _on_video_jump_to_out_clicked(self):
        """End -- jumps to the EFFECTIVE out point (out_point if
        explicitly set, else the clip's own last frame -- same
        "None means play to the end" convention VideoPlaybackController.
        _out_limit already uses internally for playback itself)."""
        source = self._video_source
        if source is None:
            return
        out_point = self._playback_controller.out_point
        if out_point is None:
            out_point = max(0, source.frame_count - 1) if source.frame_count else 0
        self._playback_controller.pause()
        self.seek_video_frame(out_point)

    def get_source_range_for_timeline(self):
        """(name, source_path, media_kind, in_frame, out_frame) for the
        single directly-loaded/selected clip's currently MARKED range
        (per Tom: "will be necessary to be able to mark desired ranges
        and add to sequence") -- out_frame falls back to the clip's own
        last frame when nothing's been marked (_playback_controller.
        out_point is None, its own "play to the end" convention), so
        this doubles as "the whole clip" when no range was ever marked,
        same default add_clip_from_library_entry already uses. None if
        no video is currently loaded as Source -- nothing to add."""
        source = self._video_source
        if source is None:
            return None
        in_frame = self._playback_controller.in_point
        out_frame = self._playback_controller.out_point
        if out_frame is None:
            out_frame = max(0, source.frame_count - 1) if source.frame_count else in_frame
        return Path(source.path).stem, source.path, "video", in_frame, out_frame

    def _on_video_add_to_sequence_clicked(self):
        range_info = self.get_source_range_for_timeline()
        if range_info is None:
            return
        self.add_source_range_to_timeline_requested.emit(*range_info)

    def refresh_video_transport_visibility(self):
        """Shows the transport bar whenever there's ANYTHING for it to
        play: a directly-loaded video (self._video_source, SOURCE mode's
        own domain) OR the Timeline has at least one clip (RECORD mode's
        domain, see _active_transport_controller). Called from load_
        video/_close_video_source (single-clip load/unload) and wired in
        main_window.py to TimelinePanel.timeline_changed (Timeline add/
        remove/clear/load) -- either source of change needs to
        re-evaluate this independently, since a Timeline built without
        ever loading a single video directly must still make the
        transport visible.

        Also the hook point for keeping RECORD mode's scrub-bar RANGE
        (per direct feedback: "the scrub bar seems to only represent a
        single clip in the timeline. it should represent the whole
        timeline") and the Source timecode field both in sync with the
        Timeline's own clip list -- a clip added/removed/reordered
        changes total_frame_count and possibly what's under the
        playhead, and this is already called on every such change via
        the timeline_changed wiring above, so there's no need for a
        second, separate hook."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        has_timeline_clips = bool(timeline_obj and timeline_obj.clips)
        self._video_transport_widget.setVisible(self._video_source is not None or has_timeline_clips)
        self._refresh_scrub_slider_for_mode()
        self._refresh_source_timecode_for_mode()

    def _update_viewer_readout(self):
        """"Fit 47% · Wipe off" -- how the picture is being shown right
        now (zoom in physical pixels, so 100% means actual pixels)."""
        if not hasattr(self, "_viewer_readout"):
            return
        scale = self.compare.display_scale()
        parts = []
        if scale is not None:
            percent = f"{scale * 100:.0f}%"
            parts.append(f"Fit {percent}" if self.compare.zoom() == 1.0 else percent)
        parts.append("Wipe on" if self._wipe_enabled else "Wipe off")
        self._viewer_readout.setText("  ·  ".join(parts))
