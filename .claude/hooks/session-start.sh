#!/bin/bash
# Starts a virtual X display (Xvfb) + Mesa software GL so PySide6/Qt's
# QOpenGLWidget-based panels (the 3D LUT viewer, GPU shader stages) can
# create a real OpenGL context and actually render during a Claude Code
# web session, with no physical GPU available. Confirmed 2026-10-04:
# QT_QPA_PLATFORM=offscreen (this repo's existing fast-test-suite
# convention, left untouched) can't create a GL context at all; Xvfb +
# LIBGL_ALWAYS_SOFTWARE=1 (llvmpipe) can.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Qt's xcb platform plugin dlopen()s these at runtime rather than linking
# them directly, so a missing one only surfaces as "could not load the Qt
# platform plugin 'xcb'" with no ldd-visible symptom -- install up front.
MISSING_PKGS=""
for pkg in xvfb libxcb-cursor0 libxcb-icccm4 libxcb-util1 libxcb-image0 \
           libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
           libxkbcommon-x11-0; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    MISSING_PKGS="$MISSING_PKGS $pkg"
  fi
done
if [ -n "$MISSING_PKGS" ]; then
  apt-get update -qq
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $MISSING_PKGS
fi

# Idempotent: Xvfb's own lock file is the standard way to tell whether a
# given display number is already live (e.g. a hook re-run on "resume").
if [ ! -e /tmp/.X99-lock ]; then
  Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 &
  disown
  sleep 1
fi

# QT_QPA_PLATFORM, when a command sets it explicitly (as this repo's
# pytest suite always does: QT_QPA_PLATFORM=offscreen), overrides Qt's
# platform auto-detection regardless of DISPLAY -- so the existing fast
# offscreen test suite is unaffected by these being set session-wide.
# Anything that does NOT set QT_QPA_PLATFORM (a manual GUI smoke run,
# `python3 main_window.py`) now gets a real, software-rendered display
# by default instead of crashing for lack of one.
{
  echo 'export DISPLAY=:99'
  echo 'export LIBGL_ALWAYS_SOFTWARE=1'
} >> "$CLAUDE_ENV_FILE"
