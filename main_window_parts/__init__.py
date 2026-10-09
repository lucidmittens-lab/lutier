"""main_window_parts -- MainWindow's methods, grouped by area into mixins
that main_window.MainWindow inherits (so they all share one `self`).

Split out of main_window.py (2026-10-09), organisation only. main_window.py
stays the entry point (`python3 main_window.py`) and keeps MainWindow's
__init__, its Qt event overrides, main() and build_window(). Nothing in
this package may import main_window: run as a script it is `__main__`, and
a second import would load a duplicate copy of the module.
"""
