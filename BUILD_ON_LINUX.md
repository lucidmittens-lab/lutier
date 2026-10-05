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

On Fedora/RHEL/Rocky (dnf) -- verified on Rocky Linux 10.2 on
2026-10-04 (all were already present on a stock Workstation install;
there is no separate `xcb-util-xkb` package, `libxcb-xkb.so` ships in
`libxcb` itself):

```
sudo dnf install \
  xcb-util-cursor xcb-util-wm xcb-util xcb-util-image \
  xcb-util-keysyms xcb-util-renderutil libxcb \
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
python3 -m venv .venv          # .venv/ is gitignored
.venv/bin/pip install -r requirements.txt pytest
./run_linux.sh                 # the app
./run_linux.sh test            # the pytest suite (offscreen)
```

`run_linux.sh` exists because of two real-GPU-desktop gotchas found on
a Rocky 10.2 / NVIDIA RTX 3080 Ti / GNOME Wayland box (2026-10-04):

- **Wayland + NVIDIA gives an OpenGL ES context.** Native-Wayland Qt
  goes through EGL and gets `OpenGL ES 3.2 NVIDIA`, so every GLSL 1.20
  shader fails (`error C0201: unsupported version 120`). Running through
  XWayland (`QT_QPA_PLATFORM=xcb`) gets desktop GL 4.6 compatibility
  profile instead, and that works. On top of that, PyOpenGL picks its
  *EGL* backend whenever `XDG_SESSION_TYPE=wayland`, which doesn't match
  Qt's GLX context under xcb (`OpenGL.error.Error: Attempt to retrieve
  context when no valid context`), so `PYOPENGL_PLATFORM=glx` is needed
  as well. The launcher sets both.
- **The offscreen test suite segfaults if `DISPLAY` is set.** Qt's
  `offscreen` plugin uses GLX opportunistically when a display is
  available, so `QOpenGLWidget` tests (e.g. `test_gl_reparent_safety.py`)
  crash in the NVIDIA driver instead of getting the no-GL stub that the
  suite was written against. `run_linux.sh test` unsets
  `DISPLAY`/`WAYLAND_DISPLAY` first. Result: 1231 passed, and 7 failed
  in `test_airplay_output_sink.py`, which is expected because those
  tests need macOS/PyObjC.

The GL-dependent tests (`test_gpu_*.py`, `test_wipe_compare_widget*.py`)
need a real context, so run them against the actual display/GPU:

```
QT_QPA_PLATFORM=xcb PYOPENGL_PLATFORM=glx .venv/bin/python -m pytest -q \
  test_gpu_*.py test_wipe_compare_widget*.py test_gl_reparent_safety.py
```

First real-GPU run (2026-10-05) found, and fixed:

- **Viewer drew into the bottom-left quarter at 200% display scaling** --
  `WipeCompareWidget` passed logical sizes to a device-pixel
  framebuffer. Now multiplied by `devicePixelRatioF()`.
- **Window grew off the bottom of the screen on loading a LUT** -- the
  Parameters inspector had no scroll area, so its current page's full
  height became the window's minimum. Now scrolls.
- **Black row+column grid in the Lanczos resample on NVIDIA** -- the
  kernel's `x == 0.0` guard missed a tiny nonzero x at texel centers,
  giving NaN. Now an epsilon guard.

- **GPU LUT lookups ~1.1e-3 off on NVIDIA** -- hardware trilinear
  filtering uses ~8-bit fixed-point weights (Apple/llvmpipe filter in
  full float, so it never showed before). `gpu_lut_shader.py` now does
  the trilinear blend itself from exact lattice fetches; GPU now matches
  the CPU path to <1e-6.
- **Launch size** -- GNOME/XWayland drops a maximize requested before
  the window is mapped; `show_on_launch` re-requests it.
- **MXF** wasn't an accepted import extension, and ARRI MXF reports no
  `average_rate` (now falls back to `guessed_rate`).

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

## 3. Setting a real-GPU Linux box up as the test bed

For a machine with an actual monitor and GPU attached (not this sandbox)
that Claude Code also runs on directly -- the setup that gets the most
out of real hardware, per Tom (2026-10-05): "I have Claude code installed
on a Linux box. How can I set it up to best be our test bed."

**Feature-level/regression testing needs nothing new.** This repo's own
test suite already drives clicks, drags, and drops entirely in-process,
independent of whatever display backend is running underneath --
`PySide6.QtTest.QTest.mousePress/mouseMove/mouseRelease` posts real Qt
events straight into the widget's event queue (see
`test_timeline_track_widget.py`'s drag-to-trim/reorder tests for the
established pattern), and the cross-panel Media Panel -> Timeline
drag-and-drop is tested by constructing a `QDragEnterEvent`/`QDropEvent`
directly and calling `widget.dragEnterEvent(event)`/`dropEvent(event)`
rather than simulating a real OS-level drag gesture (same file, the
`test_drop_emits_media_entry_drop_requested_*` tests). Both patterns
work identically under `QT_QPA_PLATFORM=offscreen`, the sandbox's
Xvfb+software-GL setup (section 2 above), or a real display -- nothing
about them depends on real hardware, so there's no reason to duplicate
this work on the Linux box specifically.

**What a real display + GPU uniquely adds is true black-box testing:**
launching the actual `python3 main_window.py` process the way you'd run
it yourself and interacting with it from OUTSIDE that process -- no
Python object handles into the running widgets, closer to how a real
user hits it, and the only way to exercise things that only manifest in
the full running app: real GPU-driven rendering (actual driver output,
not llvmpipe -- this is what makes it worth judging color/shader
correctness on), real frame timing/performance, real window-manager
focus/raise/drag-and-drop behavior (Qt's own `QDrag.exec()` runs a
genuinely different, nested native event loop than the synthetic
`QDropEvent` tests above exercise), and real input-device feel.

For that, one more tool belongs on this box specifically (no point
installing it in a sandbox with nothing to click):

```
sudo apt install xdotool imagemagick   # xdotool: real click/drag/type; `import`: screenshot a real window
```

`xdotool` is X11-only -- check `echo $XDG_SESSION_TYPE` first. Qt apps
typically still run as ordinary X11 clients through XWayland even on a
Wayland session (so this usually works either way), but on a pure
Wayland-native setup `xdotool` can't see the window at all; `ydotool` is
the Wayland-native equivalent if that turns out to be necessary, with
more setup (a running `ydotoold` daemon) and overall less mature tooling.

Basic pattern for driving the real app as a black box:

```
python3 main_window.py &
APP_PID=$!
sleep 2
WIN=$(xdotool search --name "LUTier" | head -1)   # setWindowTitle("LUTier") -- main_window.py
xdotool windowactivate "$WIN"
xdotool mousemove --window "$WIN" 400 300 click 1
import -window "$WIN" /tmp/check.png
kill "$APP_PID"
```

## 4. A real distributable build (PyInstaller/AppImage)

Not set up yet -- deliberately deferred until running from source is
confirmed solid on your end. `setup.py`'s py2app approach doesn't apply
to Linux at all (no cross-compiling a macOS `.app`, and py2app itself is
macOS-only); the equivalent for a packaged Linux build would be
PyInstaller, optionally wrapped as an AppImage for a single-file
double-clickable result. Worth doing once source-level running is
verified, not before.
