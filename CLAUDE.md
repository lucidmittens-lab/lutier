# LUTier

A PySide6 desktop colour-grading app. The goal is a free, Resolve-like tool
(media → timeline → grade) with Baselight-grade colour management: explicit
working spaces and order of operations. A grade is an ordered **Stack** of
stages (LUT, CDL, OCIO grading operators, DRTs, mattes, forks). The Viewer
renders the Stack on the GPU, and export bakes it on the CPU to
`.cube`/`.clf`/`.3dl`/… or renders stills and video. The Mac (py2app `.app`)
was the original target. This Linux box (RTX 3080 Ti, GNOME Wayland) is now
the main dev and real-GPU test bed. "Tom" in comments and docs is the
product owner.

Almost everything is flat in the repo root: about 100 app modules and 97
`test_*.py` files. The exceptions are the `stack_panel/` and
`main_window_parts/` packages (see the module map). Each module's docstring explains its purpose and history; read it
before editing the module.

## Module map

- **Core model (Qt-free):** `stack.py` (Stage/Stack, `STAGE_KINDS`,
  `_apply_stage_to_array` is the one CPU dispatch shared by preview, bake and
  export; `StageCache`; fork and matte semantics), `colour_pipeline.py`
  (`build_effective_stack`: wraps the user's Stack with input, operator-space
  and display conversions), `undo_history.py`, `canvas.py`, `id_counter.py`,
  `app_logic.py` (misc I/O and LUT helpers).
- **Grading operators (Qt-free, `apply_X(array, **params)`):** `cdl`,
  `printer_lights`, `grading_primary`, `grading_tone`, `grading_rgb_curve`,
  `exposure_contrast`, `color_warper` (WARPier), `color_balance`, `retarget`,
  mattes `keyer`, `matte_shape`, `matte_combine`, `segmentation`.
- **Colour management, OCIO and DRTs:** `aces_drt` (OCIO configs and the ACES
  output transform), `custom_drt` (CAMier), `colorspace_convert` (OCIO CSC),
  `colorspace` (colour-science spaces), `ocio_processor_cache`. LUT formats:
  `clf_adapter`, `format_writers`.
- **GPU renderer and shaders:** `gpu_stack_renderer` (`render_chain`,
  `GPU_ELIGIBLE_KINDS`, `is_stack_gpu_eligible`), `gpu_shader_prototype`
  (offscreen GL context, OCIO GLSL extraction), `gpu_shader_cache`,
  `gpu_ocio_dynamic` (OCIO dynamic-uniform kinds), one `gpu_*_shader.py` per
  special kind (`cdl`, `lut`, `aces_drt`, `custom_drt`, `printer_lights`,
  `color_warper`, `matte_shaders`, `fork_merge`), `gpu_lanczos_compositor`
  (scaling and wipe compositing).
- **UI shell:** `main_window` (the entry point, `python3 main_window.py`:
  `MainWindow.__init__`, its Qt event overrides, `main()`), whose methods live
  in `main_window_parts/` mixins: `docks`, `layout` (`_build_layout`),
  `menus`, `workspace`, `session` (sessions and project settings), `undo`,
  `pipeline` (stack changes, effective stack, Sequence/Shot),
  `timeline_wiring`, `export_lut`, `export_video`, `constants`
  (`PANEL_TOGGLES`). Parts never import `main_window` (run as a script it is
  `__main__`). Also `splash_launcher` (packaged entry point), `style` (all QSS
  and the palette), `theme_prefs`, `panel_frame`, `floating_dialog`,
  `hover_inspector`, `spinbox_scrub`, `help_text` (in-app manual),
  `bake_settings_form`, `transport_icons`.
- **UI panels and widgets:** `stack_panel/` (the Stack list plus every
  Parameters inspector page; see below), `sample_image_panel` (Record viewer)
  and `source_viewer`, `wipe_compare_widget` (the GL Viewer surface),
  `browser_panel`, `media_panel`, `lut_library_panel`, `stack_memories_panel`
  (Stills Gallery), `lut_viewer_3d` (Graph panel), `curve_editor_widget`,
  `color_wheel_widget`, `color_warper_wheel`, `color_warper_cloud_widget`,
  `safe_gl_view_widget`.
- **`stack_panel/` package:** `panel.py` (the `StackPanel` class: list UI, ☰
  menu, selection, add/reorder/delete, public API, inspector dispatch),
  `labels.py` (Qt-free field specs, row labels, Alt+hover text), `rows.py`,
  `list_widget.py` (drag and drop), `widgets.py`, and one mixin per group of
  inspector pages, `inspect_{lut,drt,cdl,primary,tone,warper,mattes,fork}.py`.
  `StackPanel` inherits the mixins, so all pages share one `self`. Outside
  code imports from `stack_panel` (`__init__` re-exports).
- **Media:** `image_io` (OpenImageIO), `video_source` (PyAV), `media_probe`,
  `media_library`, `media_cache`, `thumbnail`, `video_export`.
- **Timeline and playback:** `timeline`, `timeline_panel`,
  `timeline_track_widget`, `timeline_playback_controller`,
  `video_playback_controller`, `frame_server` (decode-ahead thread),
  `transport_logic`, `timecode`, `video_scrub_slider`, `edl`.
- **Session and file I/O:** `session_io` (`.ltr` JSON, versioned, old versions
  must still load), `workspace_io` (dock layout, kept separate from the
  session), `stack_memory`/`stack_memory_file_io` (`.ltm`), `lut_library`,
  `cdl_library`, `cdl_file_io`, `atomic_write`.
- **Scopes:** `scopes` (math), `scopes_panel`, `scopes_worker` (off-UI-thread),
  `volumetric_scope`, `volumetric_scope_widget`.
- **External output:** `casting_controller`, `video_output_sink`,
  `airplay_output_sink` (macOS/PyObjC only), `ycbcr_convert`.

## Running the app and tests

Use the repo's `.venv` (Python 3.12). On Linux, `run_linux.sh` wraps both:

```sh
./run_linux.sh                         # run the app on the real display
./run_linux.sh test                    # full suite, offscreen, parallel (~13 s)
./run_linux.sh test test_stack.py      # one module
./run_linux.sh test test_stack.py -k fork
./run_linux.sh test -n 0 -x test_stack.py   # one process: for --pdb, -x, print debugging
./run_linux.sh test-gl                 # the GL-context tests, on the real display
.venv/bin/python test_stack.py         # most test files also run directly
```

Test deps: `pip install -r requirements.txt -r requirements-dev.txt`.

`run_linux.sh test` is `QT_QPA_PLATFORM=offscreen pytest` with `DISPLAY` and
`WAYLAND_DISPLAY` unset; defaults live in `pytest.ini`. Offscreen with a live
`DISPLAY` makes Qt try GLX, which segfaults on NVIDIA. Under offscreen,
`QOpenGLWidget.isValid()` is always False, so GL-backed widgets in the main
suite (Viewer, Graph, scopes) test wiring only, never pixels.

**Parallel runs.** When pytest-xdist is installed, `conftest.py` defaults to
`-n auto --dist loadgroup` (58 s serial → 13 s on 16 cores). An explicit `-n`
wins. Files whose tests depend on each other's on-disk state or order are in
`conftest.SAME_WORKER_FILES` and run on one worker, in file order. If a new
test fails only in parallel, it shares state with another test; add its file
there. Don't drop the `workerinput` guard in conftest's `pytest_cmdline_main`:
without it every worker spawns its own workers, a fork bomb that froze this
machine.

**Tests that need a real GL context** are `conftest.GL_TEST_FILES`: the
`test_gpu_*` files, `test_wipe_compare_widget_gpu_state.py` and
`test_wipe_compare_widget_readback.py`. Their GL contexts break pyqtgraph's
class-level shader cache, so they never share a process with the rest of the
suite. A normal run skips them. `pytest --gl` (what `run_linux.sh test-gl`
runs, with `QT_QPA_PLATFORM=xcb PYOPENGL_PLATFORM=glx`) runs only them, marked
`gl`. Naming one file also works:

```sh
QT_QPA_PLATFORM=xcb PYOPENGL_PLATFORM=glx .venv/bin/python -m pytest test_gpu_stack_renderer.py
```

In Claude Code web sessions, `.claude/hooks/session-start.sh` provides Xvfb
plus Mesa (`DISPLAY=:99`, `LIBGL_ALWAYS_SOFTWARE=1`), so the same commands
work there. Keep that hook working.

Slowest tests (about 1 to 1.6 s each) are LUT bakes and resamples in
`test_lut_controls.py`, `test_printer_lights.py` and `test_stack.py`;
check with `--durations=20`.

Known failure: the 7 tests in `test_airplay_output_sink.py` need macOS and
PyObjC, and fail on Linux.

## Conventions

- **Qt-free backend + thin Qt wrapper.** Maths and I/O modules never import
  Qt, so they are unit-testable. Panels call them.
- **Adding a stage kind** touches: `stack.STAGE_KINDS`, a `make_X_stage`
  factory, `_apply_stage_to_array` (this alone gives bake and export), the
  `session_io` save/load entries, an inspector page (`_build_X_inspector`,
  handlers, `_apply_X_live` and `_refresh_X_inspector` in a
  `stack_panel/inspect_*.py` mixin, registered in `_build_params_ui` and the
  `_refresh_inspector_body` lookup), menu entry, row label and Alt+hover card
  (`stack_panel/labels.py`), and optionally the GPU side (see next item).
- **The GPU mirrors the CPU, and the CPU is the truth.** Export, bake and
  Scopes always use the CPU path. A GPU stage is added to
  `GPU_ELIGIBLE_KINDS`, `_program_for_stage` and `_set_uniforms_for_stage`, and
  must match the CPU output in a pixel-diff test (typically 1e-4 to 1e-5).
  If any enabled stage is ineligible, the whole stack falls back to the CPU.
  Shader caches are keyed on **structure** (kind, style, config, LUT `id()`)
  and never on slider values. Live values are pushed as uniforms.
- **GLSL 1.20 / OpenGL 2.1 Compatibility Profile only:** `varying`,
  `texture2D`, `gl_FragColor`, real VBOs. Don't add a `QSurfaceFormat` or Core
  Profile, because pyqtgraph doesn't bind a VAO.
- **User-data writes go through `atomic_write.atomic_open`:** `.ltr`, `.ltm`,
  the workspace file and theme prefs.
- **Undo is debounced:** `stack_changed` fires on every drag tick, and
  `MainWindow` coalesces pushes with `_UNDO_DEBOUNCE_MS`. Call
  `_flush_pending_undo_push()` before reading or saving history, and cancel
  the pending push when loading a new stack. Programmatic inspector refreshes
  set `_refreshing_inspector` so they don't feed back as edits.
- **Panels:** every panel is a `QDockWidget` (`_make_dock`). The ☰ corner menu
  and the menu-bar menu are both filled by the same `populate_menu(menu)`.
  Stack selection is tracked by stage id (`_selected_stage_id`), not by row.
  Each inspector page has one outer `QGroupBox`.
- **Tests:** `test_<module>.py`, plain `assert` functions, a local `_app()`
  helper that builds the QApplication, and an `if __name__ == "__main__"`
  runner. Anything that constructs `MainWindow` must first redirect
  `workspace_io.DEFAULT_WORKSPACE_DIR`/`DEFAULT_WORKSPACE_PATH` to a temp dir
  (see `test_undo_debounce.py`). Use `tempfile`, never fixed `/tmp/...`
  paths.
- **The manual lives in two places.** `help_text.USER_MANUAL_MARKDOWN` is what
  Help > Manual shows, and it is the current copy. `docs/USER_MANUAL.md` has
  drifted since 2026-08-16. Update both by hand.

## Don't do this (learned the hard way)

- **GL state:** never "reset" by binding framebuffer 0. Inside `paintGL`, Qt
  renders to its own FBO. Save `GL_FRAMEBUFFER_BINDING` and restore it, or the
  Viewer goes black.
- **GL context:** never make a GL call without a current context. That
  includes eligibility checks: `is_*_gpu_eligible` must stay GL-free, or you
  get a SIGSEGV. Readback outside paint must use `makeCurrent`/`doneCurrent`,
  as `read_back_after_frame` does. Don't add work to
  `WipeCompareWidget.paintGL`.
- **Cache keys:** don't key one cache on two things that invalidate
  separately. The Viewer once re-uploaded the full-resolution source texture
  on every slider tick. The upload fingerprint and the stack fingerprint are
  separate on purpose.
- **OCIO on the GPU:** bind every texture the `GpuShaderDesc` reports. HDR
  ACES views need a 2D LUT texture; leaving it unbound gave a "milky" Viewer.
  Don't expose non-dynamic OCIO properties as sliders, because each change
  forces a recompile. `GradingBSplineCurve.getControlPoints()` returns copies,
  so rebuild the curve from the flat list instead.
- **CPU cost:** `colour.LUT3D.apply` takes 0.5 to 3 s at native resolution.
  CPU recomputes for Scopes on GPU-eligible stacks stay capped at
  `SCOPES_MAX_DIMENSION`. Treat LUT objects as immutable: they are
  fingerprinted by `id()`.
- **pyqtgraph and GL widgets:** `AA_ShareOpenGLContexts` must be set before
  any `QApplication` exists (done in `main()` and `conftest.py`). In
  `rebuild_gl_view`, reset the items to `None` before reassigning `gl_view`.
  `GLLinePlotItem` colours are floats from 0 to 1. Switch views with
  `QStackedWidget` pages, not `setVisible`, or the panel resizes.
- **Widget lifetime:** after `deleteLater()`, keep a Python reference until
  Qt deletes the widget (`_pending_gl_view_deletions`). Otherwise the GC can
  free it first, and you get a double free.
- **Colour spaces:** colour-science's registry names are ambiguous for
  working spaces (some entries are gamut-only). Decode and encode through
  OCIO with `aces_drt.decode_to_linear`/`encode_from_linear`.
- **Docks:** `addDockWidget`/`splitDockWidget` don't redock a floating dock;
  dock floating panels first (`_dock_all_floating_panels`). Restore floating
  state after the event loop starts (`QTimer.singleShot`), never
  synchronously in `__init__`.
- **Styling:** `palette(placeholder-text)` is lost after polish, so use the
  literal `style.HINT_TEXT_HEX`. A plain `QWidget` or `QDockWidget` ignores
  QSS backgrounds and rounded corners unless `WA_StyledBackground` is set.
  Custom-painted widgets fill their own
  background. Check colours by pixel-sampling `grab()`, not by eye.
  Panels darker than the gaps between them is intentional.
- **Frozen app:** never find data files via `__file__`; use the
  `RESOURCEPATH` helpers (`app_logic.default_sample_image_path`,
  `style._texture_asset_path`). Never `__import__("main_window")`. Compiled
  dependencies go in `setup.py`'s `packages`, not `includes`.
- **Throttle vs debounce:** restarting a single-shot `QTimer` is a debounce
  and never fires mid-drag. For live outputs, use a leading+trailing throttle
  (`casting_controller`).
- **Process:** report "tests pass" and "confirmed on real hardware"
  separately. Don't use `sed -i.bak`. Don't `pkill -f main_window.py`, because
  it matches your own shell; kill by PID. If a run pollutes
  `~/.config/LUTier/workspace.json`, delete it.

## History and docs

- `RUNNING.md`: the long-form dev log, entries #1 to #69, through
  2026-08-08. Later history is in git.
- `docs/`: `BUILD_ON_LINUX.md`, `BUILD_ON_MAC.md`, `USER_MANUAL.md`.
- `docs/archive/`: dated checkpoints (`KNOWN_GOOD_SUMMARY_*`), the
  architecture overview, the GPU, OCIO Tier 1 and viewer-casting plans, the
  cleanup notes, and the dock-tabify bug report. Many module docstrings cite
  these plans by stage, for example "GPU_ACCELERATION_PLAN Stage 5".
