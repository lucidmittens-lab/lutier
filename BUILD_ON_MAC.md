# Building the LUTier .app on your Mac

All of this happens in Terminal, inside the `lut_tool` folder. Open
Terminal, then:

```
cd path/to/lut_tool
```

(drag the folder into the Terminal window after typing `cd ` to auto-fill
the path, if you don't want to type it out).

## 0. Test the GUI as a plain script first

Do this before packaging anything, even though the app (including the 3D
viewer) has by now been run and exercised extensively on real macOS
hardware across many sessions -- packaging can still surface its own
issues (missing bundled assets, Qt plugin bundling) independent of
whether the unpackaged script runs fine.

```
pip3 install -r requirements.txt
python3 main_window.py
```

Walk through `RUNNING.md`'s test checklist -- Choose Folder against
`samples/`, wildcard search, double-click a LUT to populate the 3D view and
info panel, drag/rotate, Controls (Retarget/Resize/Input Bounds),
Concatenate a chain, both Export paths (Current LUT and Concatenate Chain),
Export Still from the Sample Image panel. Report anything that breaks --
same loop as always -- before moving on to packaging.

## 1. Generate the icon

```
chmod +x make_icon.sh
./make_icon.sh AppIcon_source.png
```

`AppIcon_source.png` is already in this folder (the ribbon-with-echoes
design, 1024x1024). This produces `AppIcon.icns` using `sips`/`iconutil`
(built into macOS, nothing to install). `setup.py` already points at
`AppIcon.icns` -- if the icon changes again later, just rerun this command
and rebuild.

## 2. Install py2app (one-time)

```
pip3 install py2app
```

## 3. Compile the app

```
rm -rf build dist
python3 setup.py py2app
```

`rm -rf build dist` matters if you've built before -- py2app can be sticky
about picking up a newly-added icon or changed `packages` list otherwise.
This build has more to compile than the HDR tool (Qt frameworks, OpenGL,
scipy) so expect it to take noticeably longer and produce a few hundred MB
`.app` instead of a few MB. Finished app lands at:

```
dist/LUTier.app
```

Confirmed working end-to-end (single-arch build, this exact sequence) on
2026-08-08.

## 4. First launch

It's unsigned, so Gatekeeper blocks a plain double-click the first time.
Right-click the app -> Open -> Open. Only needed once per build.

If the icon still shows the generic py2app default instead of yours,
that's Finder's icon cache being stubborn, not a build problem --
`killall Finder`, or drag the app to the Desktop and back, usually clears
it.

## 5. The one failure mode worth knowing about up front

**"App won't launch, or launches then immediately quits, possibly
mentioning `cocoa` or `Qt platform plugin`."** This is the single most
likely PySide6 + py2app failure, and it's the one thing I can't
pre-verify without Qt available on my end. Qt needs its `cocoa` platform
plugin (and a handful of others) bundled inside the .app correctly, and
py2app doesn't always get this right automatically for PySide6
specifically.

`setup.py` already defends against this by putting `PySide6` (and
`pyqtgraph`, `OpenGL`, `colour`, `scipy`, `numpy`, `PIL`, `PyOpenColorIO`)
in the `packages` list rather than `includes` -- `packages` copies the
whole folder tree (binaries and Qt plugins included), `includes` only
grabs the named `.py` file. If it still fails on launch:

1. Run it from Terminal instead of double-clicking, to see the actual
   error: `dist/LUTier.app/Contents/MacOS/LUTier`
2. If the error mentions `cocoa` or missing Qt plugins, confirm
   `dist/LUTier.app/Contents/Resources/lib/python3.*/PySide6/Qt/plugins/platforms/libqcocoa.dylib`
   exists. If it's missing, `PySide6` got demoted to `includes`
   somewhere -- double check `setup.py`'s `packages` list wasn't edited.
3. If py2app genuinely can't be made to produce a working PySide6 bundle
   after a couple of tries, the standard fallback is **PyInstaller**,
   which has more actively maintained PySide6 support:
   ```
   pip3 install pyinstaller
   pyinstaller --windowed --name LUTier --icon AppIcon.icns main_window.py
   ```
   This uses different bundling internals and sidesteps py2app's Qt
   packaging quirks entirely. Tell me if you end up here and I'll tune a
   proper `.spec` file instead of relying on the default one.

## 6. Sanity check before handing off to peers

Load a real production LUT, confirm the 3D view renders, run it through
Concatenate + both export paths, and open an exported `.3dl`/`.cub` in
whichever of Lustre/Flame/Baselight you have -- those writers were
validated byte-structure-only, never round-tripped through the real
application.

## Troubleshooting

**`universal2` build fails, or you want one** -- `setup.py` currently
builds single-arch (whatever Mac you run it on), not universal2, because
of a confirmed wheel mismatch: as of 2026-08-08 the `numpy`/`scipy`
installed here (via `pip3 install -r requirements.txt` under a python.org
universal2 Python) are arm64-only wheels (checked with `lipo -info` on
their compiled `.so` files), not universal2, even though the Python
interpreter itself is a proper universal2 fat binary. A universal2
py2app build needs universal2 wheels for `numpy`/`scipy` specifically, not
just a universal2 Python (the HDR tool sidestepped this entirely by being
pure stdlib -- this one doesn't). To get a true universal2 build later:
find/build universal2 wheels for `numpy`/`scipy` (Homebrew's are typically
single-arch and won't help) and reinstall under them, then add back
`'arch': 'universal2'` in `setup.py`'s `OPTIONS`. Until then, single-arch
is fine for peer testing on Macs matching the build machine's
architecture.

**Need to change the icon again later** -- just rerun step 1 with a new
source image, then steps 3-4.

**ACES DRT stage is grayed out / "PyOpenColorIO isn't installed"** -- the
Scopes panel doesn't need this, but the Stack's ACES DRT stage type does:
`pip3 install opencolorio` (already in `requirements.txt`, so step 0's
`pip3 install -r requirements.txt` should have covered it -- if it's
still disabled after that, check for a real import error by running
`python3 -c "import PyOpenColorIO"` directly and paste whatever it says).
OCIO ships as a compiled extension, so the same single-arch/universal2
caveat as numpy/scipy applies if you hit arch-related build errors.

**Build succeeds but the window is blank / crashes on 3D view** -- check
whether it's packaging-specific (works fine via `python3 main_window.py`
but not from the built `.app`) or a real regression in `lut_viewer_3d.py`
(OpenGL widget) itself. Note whether it crashes immediately or only when a
LUT is loaded, and paste the Terminal output from running the binary
directly (step 5.1 above) so we can narrow it down.
