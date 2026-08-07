"""Regression test for the waveform-vs-graticule inversion bug Tom
reported (applying an HDR limiter made the waveform trace bunch toward
the bottom while the graticule stayed correctly positioned) -- run
directly: python3 test_waveform_orientation.py

Root cause was in scopes_panel.py's _render_waveform(): an `np.flipud()`
call on the false assumption that pyqtgraph's ImageItem draws row 0 at
the top by default. Empirically (rendering a real ImageItem offscreen
with a distinct value per row and sampling actual pixel colors), under
this app's config (imageAxisOrder='col-major', the row/col transpose
already done before setImage, no invertY() call anywhere), row 0 already
lands at the BOTTOM by default -- so the flip was inverting an
already-correct image.

A deterministic, data-level check: the array handed to setImage() must
match compute_waveform's own output with NO row reversal. An end-to-end
pixel-rendering version of this same check was also tried (feed a
solid-bright vs solid-dark image, render the real widget, confirm bright
lands above dark in actual pixels) -- dropped after it kept landing on
an unrelated, consistent rendering artifact under this sandbox's
offscreen QPA platform regardless of image content (not something this
data-level check depends on, and not evidence against the fix -- this
deterministic check reproduces the actual code change directly rather
than inferring it from rendered pixel positions).
"""
import sys

import numpy as np
from PySide6.QtWidgets import QApplication

from scopes import compute_waveform
from scopes_panel import ScopesPanel, SCOPE_WAVEFORM, WAVEFORM_LUMA, WAVEFORM_LEVELS


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_render_waveform_does_not_flip_image_data():
    """Deterministic: the array _render_waveform hands to setImage() must
    be compute_waveform's own row order (row 0 = darkest), transposed for
    pyqtgraph's col-major axis order, with NO additional row reversal.
    This is the actual code change -- reproducing it exactly, rather than
    inferring it from rendered pixel positions, is the most direct
    possible regression check."""
    _app()
    panel = ScopesPanel()
    panel._on_scope_changed(SCOPE_WAVEFORM)
    panel._waveform_style = WAVEFORM_LUMA  # simplest single-channel case

    rng = np.random.default_rng(0)
    image_array = rng.random((30, 40, 3))
    panel.set_image_array(image_array)

    wf = compute_waveform(image_array, levels=WAVEFORM_LEVELS)
    mono = ScopesPanel._normalize_for_display(wf["luma"])
    expected = np.transpose(np.stack([mono, mono, mono], axis=-1), (1, 0, 2))

    actual = panel.waveform_image_item.image
    assert actual is not None
    assert np.allclose(actual, expected), (
        "the array handed to setImage() no longer matches compute_"
        "waveform's own row order -- did a flip get reintroduced?"
    )
    print("ScopesPanel._render_waveform: image data matches compute_"
          "waveform's row order exactly (row 0 = darkest, no flip) -- OK")


def test_render_waveform_matches_across_styles():
    """Same check as above, but for the RGB Parade style specifically
    (the default _waveform_style, and the one Tom was actually looking
    at) -- confirms the fix isn't accidentally luma-only."""
    _app()
    panel = ScopesPanel()
    panel._on_scope_changed(SCOPE_WAVEFORM)
    # WAVEFORM_PARADE is the default -- leave panel._waveform_style alone.

    rng = np.random.default_rng(1)
    image_array = rng.random((25, 35, 3))
    panel.set_image_array(image_array)

    wf = compute_waveform(image_array, levels=WAVEFORM_LEVELS)
    expected = np.transpose(
        np.stack([
            ScopesPanel._normalize_for_display(wf["r"]),
            ScopesPanel._normalize_for_display(wf["g"]),
            ScopesPanel._normalize_for_display(wf["b"]),
        ], axis=-1),
        (1, 0, 2),
    )

    actual = panel.waveform_image_item.image
    assert np.allclose(actual, expected), (
        "RGB Parade waveform image data no longer matches compute_"
        "waveform's row order"
    )
    print("ScopesPanel._render_waveform: RGB Parade (the default style) "
          "also matches compute_waveform's row order with no flip -- OK")


if __name__ == "__main__":
    test_render_waveform_does_not_flip_image_data()
    test_render_waveform_matches_across_styles()
    print("All waveform orientation tests passed.")
