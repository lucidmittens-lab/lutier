"""The GL viewer at FRACTIONAL device pixel ratios (e.g. QT_SCALE_FACTOR=0.75) --
run directly: QT_QPA_PLATFORM=xcb PYOPENGL_PLATFORM=glx python3 -m tests.test_wipe_compare_widget_hidpi

Needs a real GL context (conftest.GL_TEST_FILES; ./run_linux.sh test-gl).
QT_SCALE_FACTOR is only read when a QApplication is created, so each
ratio runs the viewer in its own subprocess (_PROBE) and reports back.

Checks, per ratio:
- the size paintGL draws into is exactly what Qt allocated for the
  framebuffer -- qRound(logical x dpr), with the fractional ratio used
  unrounded (hidpi.physical_size) -- and matches grabFramebuffer();
- at 100% (zoom_to_actual_pixels) one image pixel lands on exactly one
  physical pixel: the framebuffer region equals the source to within
  8-bit rounding, with no blur and no off-by-one shift.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent

_PROBE = r'''
import json, sys
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
app = QApplication(sys.argv)
import hidpi
from wipe_compare_widget import WipeCompareWidget

img = np.random.default_rng(7).uniform(0, 1, size=(97, 131, 3)).astype(np.float32)
w = WipeCompareWidget()
w.resize(401, 301)
w.set_after(img, gpu_stack=None)
w.set_wipe_enabled(False)
w.show()
for _ in range(20):
    app.processEvents()
dpr = w.devicePixelRatioF()
out = {"dpr": dpr, "target": list(w._last_physical_target),
       "expected": list(hidpi.physical_size(w.width(), w.height(), dpr))}
w.zoom_to_actual_pixels()
fb = w.grabFramebuffer()
out["framebuffer"] = [fb.width(), fb.height()]
l, t, rw, rh = w._last_physical_image_rect
out["rect"] = [l, t, rw, rh]
fb = fb.convertToFormat(fb.Format.Format_RGB888)
rows = np.frombuffer(fb.constBits(), dtype=np.uint8).reshape(fb.height(), fb.bytesPerLine())
pixels = rows[:, :fb.width() * 3].reshape(fb.height(), fb.width(), 3)
shown = pixels[t:t + rh, l:l + rw].astype(np.int32)
source = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.int32)
out["max_diff"] = int(np.abs(shown - source).max()) if shown.shape == source.shape else None
print("PROBE " + json.dumps(out))
'''


def _probe(scale_factor):
    env = dict(os.environ, QT_SCALE_FACTOR=str(scale_factor),
               PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    result = subprocess.run([sys.executable, "-c", _PROBE], cwd=REPO, env=env,
                            capture_output=True, text=True, timeout=120)
    lines = [l for l in result.stdout.splitlines() if l.startswith("PROBE ")]
    assert lines, f"probe produced no result:\n{result.stdout}\n{result.stderr[-2000:]}"
    return json.loads(lines[-1][len("PROBE "):])


def _check(scale_factor):
    out = _probe(scale_factor)
    assert abs(out["dpr"] - scale_factor) < 1e-9 or out["dpr"] % 1 != 0, out
    assert out["target"] == out["expected"] == out["framebuffer"], (
        f"paintGL target {out['target']} must equal Qt's framebuffer "
        f"{out['framebuffer']} = qRound(logical x {out['dpr']}) {out['expected']}")
    assert out["rect"][2:] == [131, 97], f"100% must draw the image 131x97 physical px: {out}"
    assert out["max_diff"] is not None and out["max_diff"] <= 1, (
        f"100% must map image pixels 1:1 onto physical pixels: {out}")
    return out


def test_viewer_renders_at_exact_physical_size_and_1to1_at_dpr_0_75():
    out = _check(0.75)
    assert out["framebuffer"] == [301, 226]  # 401 x 301 logical at 0.75
    print(f"viewer at dpr {out['dpr']}: framebuffer {out['framebuffer']}, 100% exact -- OK")


def test_viewer_renders_at_exact_physical_size_and_1to1_at_dpr_1_25():
    out = _check(1.25)
    assert out["framebuffer"] == [501, 376]  # 401 x 301 logical at 1.25
    print(f"viewer at dpr {out['dpr']}: framebuffer {out['framebuffer']}, 100% exact -- OK")


if __name__ == "__main__":
    test_viewer_renders_at_exact_physical_size_and_1to1_at_dpr_0_75()
    test_viewer_renders_at_exact_physical_size_and_1to1_at_dpr_1_25()
    print("\nAll wipe_compare_widget hidpi tests passed.")
