# Running LUTier on Windows

Windows is the main dev and real-GPU test bed as of 2026-10-11 (same
RTX 3080 Ti as the old Linux box, Windows 11, NVIDIA driver 617.14). The
app code needed no changes: `requirements.txt` installs from Windows
wheels, and `workspace_io._default_workspace_dir()` already resolved
Windows to `%APPDATA%\LUTier`. There's no packaged Windows build yet
(that would be PyInstaller; `setup.py` is py2app, macOS only).

## 1. Install Python and the dependencies

Python 3.12, from python.org or winget (user scope, no admin):

```
winget install --id Python.Python.3.12 --scope user
```

Then, in PowerShell in the repo folder:

```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

The Linux venv (`bin/`, `home = /usr/bin`) can't run here. If one was
copied over with the repo, the first Windows setup renamed it to
`.venv-linux/` (gitignored).

## 2. Run the app and tests

```
.\run_windows.ps1                 # the app
.\run_windows.ps1 test            # the pytest suite (offscreen, parallel, ~22 s)
.\run_windows.ps1 test-gl         # the GL-context tests, on the real display
.\run_windows.ps1 test tests\test_stack.py -k fork
```

If PowerShell refuses to run the script (execution policy), run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call
`powershell -ExecutionPolicy Bypass -File .\run_windows.ps1`.

Unlike Linux, nothing needs overriding. Qt's native `windows` platform
gives a desktop GL 4.6 compatibility context through WGL, PyOpenGL
picks WGL by itself, and Qt's `offscreen` plugin has no GLX to stumble
into, so the main suite only sets `QT_QPA_PLATFORM=offscreen`.

First Windows run (2026-10-11): 1363 passed, 8 skipped (the macOS-only
`test_airplay_output_sink.py`), and all 120 GL tests passed on the real
GPU. What needed fixing was only in tests:

- `test_format_writers.py` and `test_image_preview.py` wrote to fixed
  `/tmp/...` paths, which don't exist on Windows. They now use `tempfile`.
- `test_theme_prefs.py` made a write fail by making the directory
  read-only with `chmod`, which Windows ignores for directories. It now
  fails `os.replace` instead.
- `test_wipe_compare_widget_hidpi.py` assumed the OS ratio was 1. At
  200% Windows display scaling, `QT_SCALE_FACTOR=0.75` gave a ratio of
  1.5. The probe now sets `QT_ENABLE_HIGHDPI_SCALING=0`, so the factor
  is the whole ratio.

## Windows notes

- Preferences, the remembered workspace, the media cache prefs and the
  GPU shader cache live under `%APPDATA%\LUTier` (the Linux
  `~/.config/LUTier`). To check that a test run leaves no trace there,
  run the suite and confirm `Test-Path $env:APPDATA\LUTier` is unchanged.
- `.venv\Scripts\python.exe` is a launcher stub that starts the real
  interpreter as a child process, so a PID from `Start-Process` is the
  stub's. Kill the whole tree, to be safe (`taskkill /T /F /PID <pid>`), and check
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` for leftovers.
- Use `tempfile` in tests, never `/tmp` (it resolves to `\tmp` on the
  current drive, which usually doesn't exist).
