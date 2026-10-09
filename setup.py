"""
py2app build config for LUTier.

This MUST be run on macOS -- py2app cannot cross-compile a .app bundle from
another OS. See docs/BUILD_ON_MAC.md for the exact steps.

Build with:
    python3 setup.py py2app
"""
from setuptools import setup

APP = ['splash_launcher.py']
# splash_launcher.py (not main_window.py directly, as of the splash-screen
# work) -- shows a QSplashScreen using its own minimal PySide6-only
# imports, then does the slow `import main_window` (colour-science/
# pyqtgraph/OpenColorIO/scipy all load transitively through THAT module's
# own top-level imports) while the splash is already on screen. See
# splash_launcher.py's own docstring. `python3 main_window.py` directly
# still works too (skips the splash) -- that entry point is unaffected,
# this only changes what the PACKAGED .app launches.
# Bundled default reference still (see app_logic.default_sample_image_path)
# plus the herringbone background texture and grain separator texture
# (see style.py's _texture_asset_path, same RESOURCEPATH-anchoring logic
# for the same reason) -- 'assets' as the destination dir keeps all
# three under Contents/Resources/assets, mirroring the assets/ layout
# this checkout uses, so both path helpers resolve the same way
# (Path(__file__).parent / "assets" / ... when unfrozen, RESOURCEPATH /
# "assets" / ... when frozen) whether running from source or from the
# packaged .app.
DATA_FILES = [
    (
        'assets',
        [
            'assets/default_sample_image_acescct_ap1.tif',
            'assets/bg_herringbone.png',
            'assets/border_grain.png',
        ],
    ),
    (
        # Bundle root ('' -> Contents/Resources directly), matching where
        # this file already sits relative to setup.py in source -- not
        # under 'assets' above, since it isn't one of this app's runtime
        # UI assets, just the icon source splash_launcher.py also happens
        # to reuse at runtime now (see that file's _asset_path).
        '',
        ['AppIcon_source.png'],
    ),
]
OPTIONS = {
    'argv_emulation': False,
    # PySide6, pyqtgraph, PyOpenGL, colour-science, colour_clf_io, numpy,
    # scipy, and PIL all go in `packages`, not `includes`. py2app copies
    # `packages` entries as whole folders (binaries, Qt plugins, .so files
    # and all), whereas `includes` only grabs the named .py module -- for a
    # pure-Qt app like the HDR patcher that distinction didn't matter, but
    # here it's the difference between the Cocoa platform plugin actually
    # being in the bundle or not. If the built app fails to launch with
    # "could not find or load the Qt platform plugin 'cocoa'", it means
    # something got demoted to `includes` and needs to move back up here.
    'packages': [
        'PySide6',
        'pyqtgraph',
        'OpenGL',
        'colour',
        'colour_clf_io',
        'scipy',
        'numpy',
        'PIL',
        # OCIO ships a compiled extension module plus its built-in ACES
        # configs baked into the binary -- same 'packages' (not
        # 'includes') reasoning as PySide6 above: this needs to be copied
        # as a whole folder, binaries included, or the ACES DRT panel
        # will fail to import at runtime even though it worked in `python3
        # main_window.py` during testing.
        'PyOpenColorIO',
        # OpenImageIO -- image_io.py's real 8/10/12/16-bit + float image
        # read/write (Tom, 2026-08-13: Canvas Settings' Bit Depth needs
        # real teeth, not just an export-time label). Another compiled
        # ASWF extension module, same 'packages' (not 'includes')
        # reasoning as PyOpenColorIO immediately above.
        'OpenImageIO',
        # PyAV -- video_source.py's video decode (Tom, 2026-08-13: "Video
        # Support (viewing and rendering)"). Another compiled extension
        # module (bundles its own ffmpeg/libav libraries), same
        # 'packages' (not 'includes') reasoning as PyOpenColorIO/
        # OpenImageIO above.
        'av',
        # PyObjC -- airplay_output_sink.py's AirPlay casting window (see
        # docs/archive/VIEWER_CASTING_PLAN_2026-08-11.md). Same 'packages' (not
        # 'includes') reasoning as PySide6/PyOpenColorIO above: these are
        # compiled extension modules, not plain .py files -- without this,
        # a frozen build would still LAUNCH fine (airplay_output_sink.py's
        # own try/except ImportError degrades gracefully), but casting
        # would silently report unavailable in the shipped app even
        # though it works from source, since modulegraph's scan can't be
        # trusted alone for a package only ever imported inside a guarded
        # try/except (see that module's own docstring). 'objc' is
        # pyobjc-core's own importable module name; CoreAudio is a real
        # transitive dependency AVFoundation itself pulls in (confirmed
        # via `pip3 install pyobjc-framework-AVFoundation`'s own resolved
        # dependency list).
        'objc',
        'Cocoa',
        'AVFoundation',
        'AVKit',
        'CoreMedia',
        'CoreAudio',
        'Quartz',
    ],
    # Our own local modules -- plain .py files alongside main_window.py.
    # Regenerated 2026-07-25 by walking main_window.py's actual import
    # graph (ast-parsed, not eyeballed) rather than hand-maintained --
    # this list had drifted: it still named 'collapsible_section' (a
    # dead file, deleted this same pass -- see docs/archive/
    # CLEANUP_AND_PERF_OBSERVATIONS_2026-07-25.md) and was missing 13 modules the app
    # genuinely imports transitively (cdl/cdl_file_io/cdl_library,
    # color_balance/color_wheel_widget,
    # colorspace_convert, floating_dialog, lut_library/
    # lut_library_panel, stack_memory_file_io, undo_history,
    # workspace_io). py2app's own modulegraph scan usually catches plain
    # `import` statements without needing this list at all, which is
    # probably why a build never visibly broke over this -- but
    # `includes` exists as the failsafe for whatever that scan misses,
    # so it should actually reflect the real dependency set. Untested
    # against a real py2app build -- this is a Linux sandbox, and
    # py2app cannot cross-compile a macOS .app from here (see this
    # file's own docstring); please do one real `python3 setup.py
    # py2app` before shipping to confirm.
    #
    # Updated again for the splash-screen work: added 'main_window'
    # itself (now imported from splash_launcher.py, the new APP entry,
    # rather than being the entry script) plus 'atomic_write' and
    # 'theme_prefs' -- two more genuinely-transitively-imported local
    # modules this same "walk the real import graph" exercise had missed
    # before either existed/was noticed.
    'includes': [
        'aces_drt',
        'airplay_output_sink',
        'app_logic',
        'atomic_write',
        'browser_panel',
        'canvas',
        'casting_controller',
        'cdl',
        'cdl_file_io',
        'cdl_library',
        'clf_adapter',
        'color_balance',
        'color_wheel_widget',
        'colorspace',
        'colorspace_convert',
        'curve_editor_widget',
        'exposure_contrast',
        'floating_dialog',
        'format_writers',
        'grading_primary',
        'grading_rgb_curve',
        'grading_tone',
        'help_text',
        'hover_inspector',
        'image_io',
        'media_library',
        'media_panel',
        'lut_library',
        'lut_library_panel',
        'lut_viewer_3d',
        'main_window',
        'ocio_processor_cache',
        'panel_frame',
        'printer_lights',
        'retarget',
        'sample_image_panel',
        'scopes',
        'scopes_panel',
        'session_io',
        'spinbox_scrub',
        'stack',
        'stack_memories_panel',
        'stack_memory',
        'stack_memory_file_io',
        # stack_panel is a package (split 2026-10-09); list its submodules
        # too, same failsafe reasoning as above.
        'stack_panel',
        'stack_panel.inspect_cdl',
        'stack_panel.inspect_drt',
        'stack_panel.inspect_fork',
        'stack_panel.inspect_lut',
        'stack_panel.inspect_mattes',
        'stack_panel.inspect_primary',
        'stack_panel.inspect_tone',
        'stack_panel.inspect_warper',
        'stack_panel.labels',
        'stack_panel.list_widget',
        'stack_panel.panel',
        'stack_panel.rows',
        'stack_panel.widgets',
        'style',
        'theme_prefs',
        'undo_history',
        'video_export',
        'video_output_sink',
        'video_playback_controller',
        'video_source',
        'wipe_compare_widget',
        'workspace_io',
        'ycbcr_convert',
    ],
    # Universal2 ('arch': 'universal2') would make the same .app run on both
    # Intel and Apple Silicon, but needs universal2 wheels for numpy/scipy --
    # confirmed 2026-08-08 that the installed numpy/scipy wheels are arm64-only
    # (single-arch), so that build fails here. Building single-arch (whatever
    # this machine is) instead; see docs/BUILD_ON_MAC.md for how to get universal2
    # wheels if wider distribution is needed later.
    # Generate this with make_icon.sh from a square source PNG. Comment
    # this line out (or leave AppIcon.icns missing) to build with the
    # default generic py2app icon.
    'iconfile': 'AppIcon.icns',
    'plist': {
        'CFBundleName': 'LUTier',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleIdentifier': 'com.local.lutier',
        'NSHumanReadableCopyright': 'Internal tool',
        # LUTier reads/writes LUT and image files via QFileDialog, not by
        # being handed a file at launch, so no CFBundleDocumentTypes needed.
        'NSHighResolutionCapable': True,
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
