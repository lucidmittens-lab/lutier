"""
conftest.py -- pytest-level setup for Qt-based tests.

Sets Qt.ApplicationAttribute.AA_ShareOpenGLContexts before any test
constructs a QApplication. See main_window.py's main() for the full
explanation of why this attribute matters: without it, pyqtgraph's
GLScatterPlotItem/GLLinePlotItem cache their compiled shader program at
the CLASS level, valid only in whichever GL context compiled it first --
so a second, independent top-level QOpenGLWidget (e.g. floating/docking
the Graph panel, which is exactly what test_gl_reparent_safety.py
exercises) fails every future paint with a real OpenGL.error.GLError.

Real end users never hit this, because main() already sets the
attribute before constructing QApplication. Test files instead build
their own QApplication directly via each file's own _app() helper and
never go through main() -- which is what let the GLError surface as
noise during test_gl_reparent_safety.py on real macOS hardware (it's
invisible under the offscreen QPA platform this project was originally
only tested under, since gl_view.isValid() is always False there and no
real GL call ever happens).

Must run before ANY QApplication is constructed -- Qt reads this
attribute exactly once, at that point -- so it's set here at import
time: pytest always imports conftest.py before collecting/running any
test module in this directory, ahead of any test's own _app() call.

docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 5 note: this project's own
GPU shaders (gpu_shader_prototype.py, gpu_cdl_shader.py,
gpu_lanczos_compositor.py) deliberately target GLSL 1.20/OpenGL 2.1
Compatibility Profile, NOT a newer Core Profile -- confirmed on real
hardware that this Mac's true default QOpenGLWidget context already IS
2.1/GLSL 1.20 (`GL_VERSION`/`GL_SHADING_LANGUAGE_VERSION`, checked
directly), and that forcing a newer Core Profile app-wide breaks the
Graph panel's pyqtgraph rendering outright (its GLScatterPlotItem/
GLLinePlotItem code never binds its own Vertex Array Object, which Core
Profile requires but Compatibility Profile doesn't -- confirmed via a
real `OpenGL.GL.shaders.ShaderValidationError: No vertex array object
bound`). So: no QSurfaceFormat override needed or wanted here -- the
platform default is already exactly right, and this comment exists so a
future reader doesn't "helpfully" add one back.

ALSO excludes the GPU-acceleration project's own test files from normal
`pytest -q` collection (see collect_ignore below) -- discovered while
building docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 3: those tests each
create/tear down their own real QOffscreenSurface/QOpenGLContext (see
gpu_shader_prototype._make_offscreen_gl_context), and running them in the
SAME process as test_undo_debounce.py's real MainWindow (with its own
live pyqtgraph GLViewWidget) reproduced a caught-but-real
`OpenGL.error.GLError` (invalid glUseProgram) in pyqtgraph's own
GLLinePlotItem paint -- the same class of class-level-shader-cache
fragility this file's AA_ShareOpenGLContexts comment above already
documents, just triggered by a second, unrelated source of GL contexts
this time rather than a float/dock reparent. Non-fatal (pyqtgraph catches
it internally) but real, and these tests are fully runnable and verified
standalone anyway (`python3 -m tests.test_gpu_cdl_shader`, `python3
-m tests.test_gpu_shader_cache`, `python3 -m tests.test_gpu_stack_renderer`, `python3
-m tests.test_gpu_lanczos_compositor`, `python3 -m tests.test_gpu_wipe_compositor`) --
excluding them from the shared-process suite avoids polluting the rest of
this project's GL-touching tests rather than trying to fully root-cause a
third-party library's documented-fragile class-level GL state.
"""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

# Test files that need a real OpenGL context (see the docstring above).
# Their tests carry the `gl` marker (registered in pytest.ini). They share
# a process with each other fine, just not with the rest of the suite --
# so a normal run skips them, and `pytest --gl` runs ONLY them (needs a
# real display: QT_QPA_PLATFORM=xcb, or Xvfb in Claude Code web sessions).
GL_TEST_FILES = [
    "test_gpu_cdl_shader.py", "test_gpu_shader_cache.py", "test_gpu_stack_renderer.py",
    "test_gpu_lanczos_compositor.py", "test_gpu_wipe_compositor.py", "test_gpu_ocio_dynamic.py",
    "test_gpu_lut_shader.py", "test_wipe_compare_widget_gpu_state.py",
    "test_gpu_printer_lights_shader.py", "test_gpu_aces_drt_shader.py",
    "test_gpu_fork_merge_shader.py", "test_gpu_custom_drt_shader.py",
    "test_gpu_color_warper_shader.py", "test_gpu_matte_shaders.py",
    "test_gpu_frame_renderer.py",
    # Needs a REAL windowing system, not just a real offscreen GL context
    # -- QOpenGLWidget.isValid() is always False and paintGL never runs
    # at all under QT_QPA_PLATFORM=offscreen (confirmed directly). Run
    # standalone, WITHOUT that env var set: `python3
    # test_wipe_compare_widget_readback.py`.
    "test_wipe_compare_widget_readback.py",
    # Runs the viewer at fractional QT_SCALE_FACTORs in subprocesses.
    "test_wipe_compare_widget_hidpi.py",
]

collect_ignore = list(GL_TEST_FILES)

# Files whose tests must all run on one worker, in file order, under
# pytest-xdist. pytest.ini's `--dist loadgroup` keeps each xdist_group
# together; every other test is spread across workers individually.
# Found by running the suite split across workers and in reverse order
# (2026-10-09); all pass serially in file order.
SAME_WORKER_FILES = [
    # Every test rewrites the same test_preview_image.png at its own size.
    "test_image_preview.py",
    # Tests share the module's scratch prefs file and rely on an earlier test
    # having cleared the cache folder an earlier-still test set.
    "test_media_cache.py",
    # Float/redock geometry tests depend on dock state left by earlier tests.
    "test_gl_reparent_safety.py",
]


# Parallel by default when pytest-xdist is installed (requirements-dev.txt),
# serial when it isn't -- so pytest.ini can't break a bare environment by
# naming xdist-only options. An explicit -n (e.g. -n 0 for pdb) wins.
# tryfirst + registered after xdist = runs before xdist's own hook of this
# name, which turns "auto" into a worker count.
# NEVER let this run on a worker: xdist resets each worker to numprocesses=
# None and then calls this hook there too, so an unguarded default makes
# every worker spawn its own workers -- an exponential fork bomb that froze
# the dev machine twice (2026-10-09).
@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    if (config.pluginmanager.hasplugin("xdist")
            and not hasattr(config, "workerinput")
            and config.option.numprocesses is None):
        config.option.numprocesses = "auto"
        if config.option.dist == "no":
            config.option.dist = "loadgroup"


# Workers derive their `loadgroup` flag (which tags collected ids with their
# xdist_group) from the command line, which doesn't carry the default chosen
# above -- so hand the controller's answer over explicitly. optionalhook:
# this hook only exists when xdist is installed.
@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["lutier_loadgroup"] = node.config.option.dist == "loadgroup"


def pytest_addoption(parser):
    parser.addoption("--gl", action="store_true",
                     help="Run only the tests that need a real OpenGL context "
                          "(conftest.GL_TEST_FILES). Needs a real display.")


def pytest_configure(config):
    # Never read or write the user's real theme_prefs.json (their saved
    # accent colour) -- point it at a per-process temp file. Tests that
    # exercise theme_prefs redirect it again themselves.
    import tempfile
    import theme_prefs
    theme_prefs.THEME_PREFS_PATH = (
        Path(tempfile.mkdtemp(prefix="lutier-test-theme-")) / "theme_prefs.json")
    workerinput = getattr(config, "workerinput", None)
    if workerinput and workerinput.get("lutier_loadgroup"):
        config.option.loadgroup = True
    if config.getoption("--gl"):
        here = Path(__file__).parent
        collect_ignore[:] = [p.name for p in here.glob("test_*.py")
                             if p.name not in GL_TEST_FILES]


# tryfirst: xdist reads xdist_group markers in its own hook of this name.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        name = item.path.name
        if name in GL_TEST_FILES:
            item.add_marker(pytest.mark.gl)
        if name in SAME_WORKER_FILES:
            item.add_marker(pytest.mark.xdist_group(name))
