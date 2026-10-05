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
