"""
py2app build config for LUTier.

This MUST be run on macOS -- py2app cannot cross-compile a .app bundle from
another OS. See BUILD_ON_MAC.md in this folder for the exact steps.

Build with:
    python3 setup.py py2app
"""
from setuptools import setup

APP = ['main_window.py']
# Bundled default reference still (see app_logic.default_sample_image_path)
# plus the herringbone background texture and grain separator texture
# (see style.py's _texture_asset_path, same RESOURCEPATH-anchoring logic
# for the same reason) -- 'assets' as the destination dir keeps all
# three under Contents/Resources/assets, mirroring the assets/ layout
# this checkout uses, so both path helpers resolve the same way
# (Path(__file__).parent / "assets" / ... when unfrozen, RESOURCEPATH /
# "assets" / ... when frozen) whether running from source or from the
# packaged .app.
DATA_FILES = [(
    'assets',
    [
        'assets/default_sample_image_acescct_ap1.tif',
        'assets/bg_herringbone.png',
        'assets/border_grain.png',
    ],
)]
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
    ],
    # Our own local modules -- plain .py files alongside main_window.py.
    # Regenerated 2026-07-25 by walking main_window.py's actual import
    # graph (ast-parsed, not eyeballed) rather than hand-maintained --
    # this list had drifted: it still named 'collapsible_section' (a
    # dead file, deleted this same pass -- see CLEANUP_AND_PERF_
    # OBSERVATIONS_2026-07-25.md) and was missing 13 modules the app
    # genuinely imports transitively (cdl/cdl_file_io/cdl_library/
    # cdl_library_panel, color_balance/color_wheel_widget,
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
    'includes': [
        'aces_drt',
        'app_logic',
        'browser_panel',
        'cdl',
        'cdl_file_io',
        'cdl_library',
        'cdl_library_panel',
        'clf_adapter',
        'color_balance',
        'color_wheel_widget',
        'colorspace',
        'colorspace_convert',
        'floating_dialog',
        'format_writers',
        'help_text',
        'image_library',
        'image_library_panel',
        'lut_library',
        'lut_library_panel',
        'lut_viewer_3d',
        'panel_frame',
        'printer_lights',
        'retarget',
        'sample_image_panel',
        'scopes',
        'scopes_panel',
        'session_io',
        'stack',
        'stack_memories_panel',
        'stack_memory',
        'stack_memory_file_io',
        'stack_panel',
        'style',
        'undo_history',
        'wipe_compare_widget',
        'workspace_io',
    ],
    # Build a universal binary so the same .app runs on both Intel and
    # Apple Silicon Macs. Requires a universal2 Python build AND universal2
    # wheels for numpy/scipy -- see BUILD_ON_MAC.md if this fails; the fix
    # is usually to delete this line and build single-arch instead.
    'arch': 'universal2',
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
