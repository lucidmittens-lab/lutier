"""
transport_icons.py -- vector icons for the transport and edit controls
(Tom, 2026-10-05: "things like the transport and timeline panel need to
feel professional"). Inline SVG rendered through QSvgRenderer at several
sizes, so they're crisp at any display scaling and -- unlike the Unicode
glyphs they replace -- can't fall back to a different font with a
different line height (the "transport grows when you press Play" bug).

icon(name, color) -> QIcon. Names are the keys of _PATHS; every shape is
drawn on a 24x24 grid in a single `currentColor` so one color fits the
dark theme (style.py's primary text color by default).
"""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

DEFAULT_COLOR = "#e6e6e9"  # style._TEXT_PRIMARY

# 24x24 viewBox, fill/stroke "currentColor".
_PATHS = {
    "play": '<path d="M8 5v14l11-7z" fill="currentColor"/>',
    "pause": '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/>',
    "stop": '<path d="M7 7h10v10H7z" fill="currentColor"/>',
    "step_back": '<path d="M6 6h2v12H6zM18 6v12l-9-6z" fill="currentColor"/>',
    "step_forward": '<path d="M16 6h2v12h-2zM6 6v12l9-6z" fill="currentColor"/>',
    "play_reverse": '<path d="M16 5v14L5 12z" fill="currentColor"/>',
    "jump_start": '<path d="M4 5h2v14H4zM14 5v14l-7-7zM21 5v14l-7-7z" fill="currentColor"/>',
    "jump_end": '<path d="M18 5h2v14h-2zM10 5v14l7-7zM3 5v14l7-7z" fill="currentColor"/>',
    "mark_in": '<path d="M9 4h7v2.5h-4.5v11H16V20H9z" fill="currentColor"/>',
    "mark_out": '<path d="M15 4H8v2.5h4.5v11H8V20h7z" fill="currentColor"/>',
    "go_to_in": '<path d="M4 4h5v2H6v12h3v2H4zM20 6v12l-9-6z" fill="currentColor"/>',
    "go_to_out": '<path d="M20 4h-5v2h3v12h-3v2h5zM4 6v12l9-6z" fill="currentColor"/>',
    "clear_marks": ('<path d="M5 4h5v2H7v12h3v2H5zM19 4h-5v2h3v12h-3v2h5z" fill="currentColor"/>'
                    '<path d="M9 9l6 6M15 9l-6 6" stroke="currentColor" stroke-width="2" fill="none"/>'),
    "loop": ('<path d="M7 9h9a3 3 0 0 1 0 6h-1M17 15H8a3 3 0 0 1 0-6h1" stroke="currentColor" '
             'stroke-width="2" fill="none" stroke-linecap="round"/>'
             '<path d="M14 12.5l3 2.5-3 2.5zM10 6.5L7 9l3 2.5z" fill="currentColor"/>'),
    "insert": ('<path d="M4 15h16v4H4z" fill="currentColor" opacity="0.45"/>'
               '<path d="M12 3v8M8 8l4 4 4-4" stroke="currentColor" stroke-width="2" fill="none" '
               'stroke-linecap="round"/><path d="M12 13v8" stroke="currentColor" stroke-width="1.5"/>'),
    "overwrite": ('<path d="M4 15h16v4H4z" fill="currentColor" opacity="0.45"/>'
                  '<path d="M8 13h8v8H8z" fill="currentColor"/>'
                  '<path d="M12 3v7M8.5 7l3.5 3.5L15.5 7" stroke="currentColor" stroke-width="2" '
                  'fill="none" stroke-linecap="round"/>'),
    "append": ('<path d="M3 15h11v4H3z" fill="currentColor" opacity="0.45"/>'
               '<path d="M15 15h6v4h-6z" fill="currentColor"/>'
               '<path d="M18 4v8M14.5 9l3.5 3.5L21.5 9" stroke="currentColor" stroke-width="2" '
               'fill="none" stroke-linecap="round"/>'),
    "match_frame": ('<path d="M3 6h7v12H3zM14 6h7v12h-7z" stroke="currentColor" stroke-width="2" '
                    'fill="none"/><path d="M10 12h4" stroke="currentColor" stroke-width="2"/>'),
    "split": ('<path d="M12 3v18" stroke="currentColor" stroke-width="2" stroke-dasharray="3 2"/>'
              '<circle cx="7" cy="8" r="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/>'
              '<circle cx="7" cy="16" r="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/>'
              '<path d="M9 9.5l8 6M9 14.5l8-6" stroke="currentColor" stroke-width="1.8"/>'),
    "eject": '<path d="M12 5l7 8H5zM5 16h14v3H5z" fill="currentColor"/>',
}

_SIZES = (16, 20, 24, 32, 48, 64)
_cache = {}


def names():
    return tuple(_PATHS)


def svg_text(name, color=DEFAULT_COLOR):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" color="{color}">'
            f'{_PATHS[name]}</svg>')


def icon(name, color=DEFAULT_COLOR):
    """QIcon for `name` (see _PATHS), drawn in `color`."""
    key = (name, color)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    renderer = QSvgRenderer(QByteArray(svg_text(name, color).encode("utf-8")))
    result = QIcon()
    for size in _SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    _cache[key] = result
    return result
