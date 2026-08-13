"""Tests for canvas.py -- run directly: python3 test_canvas.py

Fully Qt-free.
"""
import dataclasses

import canvas as cv


def test_default_canvas_matches_documented_defaults():
    c = cv.DEFAULT_CANVAS
    assert c.width == 1920 and c.height == 1080
    assert c.bit_depth == 8
    assert c.pixel_aspect_ratio == 1.0
    assert c.frame_rate == 24.0
    print("DEFAULT_CANVAS: matches documented 1920x1080/8-bit/1.0 PAR/24fps -- OK")


def test_canvas_settings_rejects_unknown_bit_depth():
    try:
        cv.CanvasSettings(bit_depth=9)
        raise AssertionError("expected ValueError for an unknown bit_depth")
    except ValueError:
        pass
    print("CanvasSettings: raises ValueError for an unknown bit_depth -- OK")


def test_canvas_settings_rejects_non_positive_dimensions_and_rates():
    for kwargs in ({"width": 0}, {"height": -10}, {"pixel_aspect_ratio": 0.0}, {"frame_rate": -1.0}):
        try:
            cv.CanvasSettings(**kwargs)
            raise AssertionError(f"expected ValueError for {kwargs}")
        except ValueError:
            pass
    print("CanvasSettings: raises ValueError for non-positive width/height/PAR/frame_rate -- OK")


def test_canvas_settings_is_frozen_and_replace_creates_a_new_object():
    c = cv.DEFAULT_CANVAS
    try:
        c.width = 100
        raise AssertionError("expected a frozen dataclass to reject attribute assignment")
    except dataclasses.FrozenInstanceError:
        pass
    c2 = cv.replace_canvas(c, width=3840, height=2160)
    assert c2.width == 3840 and c2.height == 2160
    assert c2.bit_depth == c.bit_depth  # untouched fields carry over
    assert c.width == 1920, "the original must be untouched -- reassign, don't mutate"
    print("CanvasSettings: frozen, and replace_canvas produces a new object without mutating the original -- OK")


def test_bit_depth_label_covers_every_bit_depth():
    for depth in cv.BIT_DEPTHS:
        c = cv.CanvasSettings(bit_depth=depth)
        assert c.bit_depth_label == cv.BIT_DEPTH_LABELS[depth]
    assert cv.CanvasSettings(bit_depth="32f").bit_depth_label == "32-bit float"
    print("CanvasSettings.bit_depth_label: covers every BIT_DEPTHS entry -- OK")


def test_canvas_from_first_media_seeds_dimensions_but_keeps_default_frame_rate_when_none():
    c = cv.canvas_from_first_media(width=1280, height=720, frame_rate=None)
    assert c.width == 1280 and c.height == 720
    assert c.frame_rate == cv.DEFAULT_FRAME_RATE
    c2 = cv.canvas_from_first_media(width=3840, height=2160, frame_rate=23.976)
    assert c2.frame_rate == 23.976
    print("canvas_from_first_media: seeds width/height, keeps DEFAULT_FRAME_RATE for a still (no frame_rate) -- OK")


def test_session_dict_round_trips():
    original = cv.CanvasSettings(width=3840, height=2160, bit_depth=16,
                                  pixel_aspect_ratio=2.0, frame_rate=23.976)
    data = cv.canvas_to_session_dict(original)
    rebuilt = cv.canvas_from_session_dict(data)
    assert rebuilt == original
    print("canvas_to_session_dict/canvas_from_session_dict: round-trips exactly -- OK")


def test_session_dict_from_none_or_empty_returns_default():
    assert cv.canvas_from_session_dict(None) == cv.DEFAULT_CANVAS
    assert cv.canvas_from_session_dict({}) == cv.DEFAULT_CANVAS
    print("canvas_from_session_dict: None/empty (older session files) -> DEFAULT_CANVAS -- OK")


def test_session_dict_from_partial_data_fills_in_defaults():
    rebuilt = cv.canvas_from_session_dict({"width": 4096})
    assert rebuilt.width == 4096
    assert rebuilt.height == cv.DEFAULT_HEIGHT
    assert rebuilt.bit_depth == cv.DEFAULT_BIT_DEPTH
    print("canvas_from_session_dict: missing keys fall back to defaults individually -- OK")


if __name__ == "__main__":
    test_default_canvas_matches_documented_defaults()
    test_canvas_settings_rejects_unknown_bit_depth()
    test_canvas_settings_rejects_non_positive_dimensions_and_rates()
    test_canvas_settings_is_frozen_and_replace_creates_a_new_object()
    test_bit_depth_label_covers_every_bit_depth()
    test_canvas_from_first_media_seeds_dimensions_but_keeps_default_frame_rate_when_none()
    test_session_dict_round_trips()
    test_session_dict_from_none_or_empty_returns_default()
    test_session_dict_from_partial_data_fills_in_defaults()
    print()
    print("All canvas tests passed.")
