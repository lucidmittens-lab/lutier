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
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
