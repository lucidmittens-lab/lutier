# Running (and eventually building) LUTier on Linux

LUTier's actual application code has no macOS dependency -- it's plain
PySide6/Qt6, numpy/scipy, OpenColorIO/OpenImageIO, same as the Mac build.
The only genuinely macOS-only piece is the **packaging** step
(`setup.py` uses py2app, which can't cross-compile a `.app` from another
OS -- see that file's own docstring), plus one now-fixed hardcoded
`~/Library/Application Support` preferences path (see
`workspace_io.py`'s `_default_workspace_dir()` -- Linux now resolves to
the XDG Base Directory spec's `$XDG_CONFIG_HOME` / `~/.config` instead).
There's no distributable Linux package yet (no PyInstaller/AppImage
build has been set up) -- this covers running from source today.

All of this happens in a terminal, inside the `lut_tool` folder:

```
cd path/to/lut_tool
```

## 0. System packages

Qt6's `xcb` platform plugin (the one it uses on a real X11/Wayland-via-
XWayland desktop) needs a handful of shared libraries that a minimal
Ubuntu/Debian install won't have by default. On Ubuntu/Debian:

```
sudo apt install \
  libxcb-cursor0 libxcb-icccm4 libxcb-util1 libxcb-image0 \
  libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
  libxkbcommon-x11-0 libxkbcommon0 \
  libgl1 libegl1 libglx0 \
  libfontconfig1 libdbus-1-3 libice6 libsm6 libxrender1 libxi6
```

(Confirmed via `ldd` against this repo's installed PySide6 wheel on
2026-10-03 -- `libqxcb.so` itself resolved fine, but it `dlopen`s several
of the `libxcb-*`/`libxkbcommon-x11-0` libraries above at runtime rather
than linking them directly, so `ldd` alone won't catch a missing one; the
symptom if one's missing is Qt refusing to start with "could not load
the Qt platform plugin 'xcb'". Everything else in the list above -- GL,
fontconfig, dbus, ICE/SM, Xrender, Xi -- was already present.)

On Fedora/RHEL/Rocky (dnf), the equivalent package names -- **not yet
verified on an actual RHEL-family machine**, mapped from the Ubuntu names
above by Qt's own known xcb-plugin dependency list:

```
sudo dnf install \
  xcb-util-cursor xcb-util-wm xcb-util xcb-util-image \
  xcb-util-keysyms xcb-util-renderutil xcb-util-xkb \
  libxkbcommon-x11 libxkbcommon \
  mesa-libGL mesa-libEGL libglvnd-glx \
  fontconfig dbus-libs libICE libSM libXrender libXi
```

If any package name above is wrong for your Rocky/RHEL release, the
error Qt gives (`could not load the Qt platform plugin "xcb"`, often
with `QT_DEBUG_PLUGINS=1 python3 main_window.py` giving the specific
missing `.so`) will usually name the exact library to track down.

## 1. Install Python dependencies and run

```
pip3 install -r requirements.txt
python3 main_window.py
```

`requirements.txt` is already fully cross-platform (PySide6, pyqtgraph,
PyOpenGL, scipy, colour-science, colour-clf-io, numpy, Pillow,
opencolorio, OpenImageIO, av) -- nothing in it is macOS- or
Windows-specific.

Walk through the same test checklist as the Mac build (`RUNNING.md`):
Choose Folder against `samples/`, wildcard search, double-click a LUT to
populate the 3D view and info panel, drag/rotate, Controls
(Retarget/Resize/Input Bounds), Concatenate a chain, both Export paths,
Export Still from the Sample Image panel, and a pass through the
Timeline/Media Library NLE features. Report anything that breaks.

## 2. GUI/GL smoke-testing without a real GPU

`QT_QPA_PLATFORM=offscreen` (this repo's standing fast-test-suite
convention -- see every `test_*.py`'s own docstring) is genuine
rasterization for plain widgets, but its `QOpenGLContext` is a stub: a
`QOpenGLWidget` (the 3D LUT viewer, GPU shader stages) fails outright
(`QOpenGLContext.create() failed.`) under it, regardless of GPU hardware.

A real OpenGL context *can* be created with no GPU at all, though,
via a virtual X display (`Xvfb`) plus Mesa's software rasterizer
(`llvmpipe`):

```
Xvfb :99 -screen 0 1920x1080x24 &
DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 python3 main_window.py
```

Confirmed 2026-10-04: this gets a real, valid OpenGL 4.5 context and the
3D viewer's lattice actually renders (software, not hardware --
correctness/color-accuracy still needs real hardware, see below, but
layout/interaction/"does it render at all" is now verifiable without
leaving the sandbox). Claude Code web sessions on this repo set this up
automatically via `.claude/hooks/session-start.sh` (`DISPLAY`/
`LIBGL_ALWAYS_SOFTWARE` land in the session environment on startup) --
anything that doesn't explicitly set `QT_QPA_PLATFORM=offscreen` now gets
a real display by default instead of crashing for lack of one; the
existing offscreen pytest suite is unaffected since it always sets
`QT_QPA_PLATFORM` explicitly, which overrides display auto-detection.

**Still genuinely needs a real machine with real GPU hardware (yours):**
pixel-accurate color output, actual render *speed*/performance judgment,
and anything input-device-dependent (real mouse/trackpad feel dragging
the 3D view, scrubbing playback). Software-rendered llvmpipe proves the
GUI *works*, not that it's fast or color-correct -- that's still
specifically what running a build on your own PC is for.

## 3. A real distributable build (PyInstaller/AppImage)

Not set up yet -- deliberately deferred until running from source is
confirmed solid on your end. `setup.py`'s py2app approach doesn't apply
to Linux at all (no cross-compiling a macOS `.app`, and py2app itself is
macOS-only); the equivalent for a packaged Linux build would be
PyInstaller, optionally wrapped as an AppImage for a single-file
double-clickable result. Worth doing once source-level running is
verified, not before.
