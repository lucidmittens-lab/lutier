#!/bin/bash
# Dev launcher for Linux (see docs/BUILD_ON_LINUX.md). Uses the repo's .venv.
#   ./run_linux.sh            run the app on the real display/GPU
#   ./run_linux.sh test [...] run the pytest suite offscreen (extra args go to pytest)
#   ./run_linux.sh test-gl [...] run the tests that need a real OpenGL context
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python

if [ "${1:-}" = "test" ]; then
  shift
  # Qt's offscreen plugin opportunistically uses GLX whenever DISPLAY is
  # set; on a real NVIDIA desktop that segfaults the QOpenGLWidget tests.
  # Hiding the display keeps offscreen a true no-GL stub, as the suite expects.
  exec env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen \
    "$PY" -m pytest "$@"
fi

if [ "${1:-}" = "test-gl" ]; then
  shift
  # The GL-context tests (conftest.GL_TEST_FILES) need a real display;
  # same XWayland/GLX choice as the app itself, see below.
  exec env QT_QPA_PLATFORM=xcb PYOPENGL_PLATFORM=glx "$PY" -m pytest --gl "$@"
fi

# On a Wayland session, native-Wayland Qt gets an OpenGL ES context (the
# app's GLSL 1.20 desktop shaders won't compile against it), so run through
# XWayland/GLX instead, and stop PyOpenGL from picking its EGL backend.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export PYOPENGL_PLATFORM="${PYOPENGL_PLATFORM:-glx}"
exec "$PY" main_window.py "$@"
