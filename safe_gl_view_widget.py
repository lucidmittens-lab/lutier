"""
safe_gl_view_widget.py -- _SafeGLViewWidget, extracted from lut_viewer_3d.py
so a second real GL point-cloud view (volumetric_scope_widget.py) can
reuse the identical, already-battle-tested class instead of duplicating
it. See lut_viewer_3d.py's own module docstring (CRASH FIX) for the full
history -- this class's own docstring below is otherwise unchanged from
its original home.

Two real GL contexts already coexist in this app today (WipeCompareWidget's
QOpenGLWidget for GPU grading, and this class's own GLViewWidget for the
Graph panel) -- a third (the volumetric scope) is not a new category of
risk, just another instance of an already-proven-safe pattern.
"""
import pyqtgraph.opengl as gl


class _SafeGLViewWidget(gl.GLViewWidget):
    """Identical to pyqtgraph's GLViewWidget, except paintGL() bails out
    before issuing a single native GL call if the widget doesn't
    currently have a valid GL context/framebuffer.

    Why this exists: pyqtgraph.opengl.GLViewWidget.paintGL() calls
    straight into raw ctypes-bound GL calls (glClear/glDrawArrays/etc via
    PyOpenGL's `GL` module) with no guard of its own. Floating or docking
    a panel means reparenting this widget (see main_window.py's
    _float_panel/_dock_panel) -- on macOS that reparent can make Qt
    deliver a paint event for this widget while its GL context is
    mid-teardown or mid-recreate, i.e. before initializeGL() has run
    against whatever context it ends up with. Calling real GL functions
    at that moment is what produced the SIGSEGV/EXC_BAD_ACCESS Tom
    reported -- a hard native crash, not a Python exception, so no amount
    of try/except here would have caught it after the fact. isValid() is
    Qt's own supported "do I actually have a usable context right now"
    check, and it's the only point at which this is preventable: check it
    BEFORE calling super().paintGL(), never after.

    Silently skips (no error, no log) rather than raising when invalid --
    this isn't an error condition, it's an expected transient state during
    reparenting, and Qt will simply deliver another real paint event once
    the widget has settled into its new parent with a valid context."""

    def paintGL(self):
        if not self.isValid():
            return
        super().paintGL()
