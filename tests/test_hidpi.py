"""Tests for hidpi.py -- run directly: python3 -m tests.test_hidpi

Qt-free. The GL-side check (the real viewer's framebuffer and 1:1
pixels at a fractional ratio) is tests/test_wipe_compare_widget_hidpi.py.
"""
import hidpi


def test_qt_round_rounds_halves_away_from_zero_unlike_python_round():
    assert hidpi.qt_round(1200.5) == 1201 and round(1200.5) == 1200
    assert hidpi.qt_round(300.75) == 301
    assert hidpi.qt_round(-2.5) == -3
    print("qt_round matches qRound, not Python's half-to-even -- OK")


def test_physical_size_uses_the_fractional_ratio_unrounded():
    # 401 x 301 logical at 0.75: 300.75 x 225.75 -> 301 x 226 (what Qt
    # allocates). Truncating (pyqtgraph's int()) would give 300 x 225, and
    # rounding the RATIO (to 1) would give 401 x 301.
    assert hidpi.physical_size(401, 301, 0.75) == (301, 226)
    assert hidpi.physical_size(801, 501, 1.5) == (1202, 752)
    assert hidpi.physical_size(801, 501, 2.0) == (1602, 1002)
    assert hidpi.physical_size(0, 0, 1.5) == (1, 1)
    print("physical_size = qRound(logical x fractional dpr) -- OK")


def test_snap_rect_moves_edges_to_whole_pixels_and_keeps_whole_widths():
    assert hidpi.snap_rect(85.4, 64.6, 131.0, 97.0) == (85, 65, 131, 97)
    assert hidpi.snap_rect(10.5, 0.25, 50.0, 20.5) == (11, 0, 50, 21)
    # Both edges on .5 (left -528.5, right 1391.5): still exactly 1920 wide.
    assert hidpi.snap_rect(-528.5, -331.5, 1920.0, 1080.0)[2:] == (1920, 1080)
    l, t, w, h = hidpi.snap_rect(3.3, 7.7, 100.25, 60.6)
    assert all(isinstance(v, int) for v in (l, t, w, h))
    print("snap_rect snaps edges to whole pixels -- OK")


if __name__ == "__main__":
    test_qt_round_rounds_halves_away_from_zero_unlike_python_round()
    test_physical_size_uses_the_fractional_ratio_unrounded()
    test_snap_rect_moves_edges_to_whole_pixels_and_keeps_whole_widths()
    print("\nAll hidpi tests passed.")
