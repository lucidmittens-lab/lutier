"""
style.py -- app-wide QSS stylesheet. Kept in one place rather than
scattered setStyleSheet() calls across widget files, so the overall look
stays consistent and there's one spot to adjust if the direction needs to
change.

Uses Qt's palette() functions (palette(mid), palette(highlight), etc.)
throughout instead of hardcoded colors, so this respects the OS's light/
dark mode rather than fighting it -- same convention already used ad hoc
in wipe_compare_widget.py and sample_image_panel.py before this file
existed. A colorist is very likely running a dark OS theme in a dim room;
this doesn't force dark mode, it just doesn't break it.

FIRST PASS on "prettify this" -- functional and consistent, but a matter
of taste, not a settled design. Apply via QApplication.setStyleSheet() in
main_window.py's main(). Easy knobs: PANEL_BORDER_RADIUS, the toggle
button checked-state colors, and panel title font size/weight below.

SECOND PASS (color/accent enrichment, 2026-07-24) -- per Tom: enrich
palette(highlight) usage rather than introduce a hardcoded brand hex.
Deliberate choice over a fixed accent (e.g. a custom amber/teal) so this
keeps respecting the OS's actual accent-color setting, not just light/
dark mode -- same "no hardcoded color" rule as the rest of this file,
just applied to more states than before. Concretely: text inputs
(QLineEdit/QComboBox/QSpinBox/QDoubleSpinBox) get a real :hover and
:focus state for the first time (previously totally static until typed
into), buttons get an accent-tinted border on hover/press rather than
just a flat gray fill, list rows get a :hover state before selection
(previously nothing happened until the click that selected them), group
box titles pick up the highlight color to tie them visually to the same
accent panel titles already use, and a selected stack stage row now
accents its label text too, not just its border, for a stronger two-cue
"this is the selected one" signal. Everything below still routes through
palette() -- no literal hex/rgb anywhere in this file.

THIRD PASS (accent expansion: sliders + scrollbars, 2026-07-24) -- per
Tom, split into an "easy" pass first and a separate texture pass later.
QSlider (printer-lights/CDL-sat/fork-blend/graticule/grid-density/cage-
opacity -- see lut_controls_panel.py, stack_panel.py, scopes_panel.py,
lut_viewer_3d.py, none of them setObjectName'd, so these are type
selectors) gets a highlight-colored fill/handle instead of the fully
unstyled native look it had before. QScrollBar (currently only ever
appears implicitly via QListWidget/QTableWidget -- see browser_panel.py/
lut_library_panel.py/media_panel.py/
stack_memories_panel.py/stack_panel.py's list/table widgets, none of which
had ANY scrollbar styling before this) gets a thin, minimal handle that
picks up the same accent on hover.

Deliberately NOT in this pass: QCheckBox/QRadioButton indicators.
Styling ::indicator at all switches Qt off native painting for the
whole subcontrol, including the checkmark glyph itself -- doing this
properly needs an actual checkmark image asset, not just palette()
colors, which is real asset-generation work, not a QSS-only tweak. That
crosses into the same territory as the texture pass Tom already asked
to split off separately, so it's deferred there rather than half-done
here. (No QRadioButton exists anywhere in this codebase currently
either way.)

FOURTH PASS (empty-space texture, 2026-07-24) -- the texture pass
flagged above, now done. Tom picked herringbone from five generated
candidates (grain/dots/diagonal/grid/herringbone -- see
texture_design/gen_textures.py and gen_herringbone.py for how the
others were made, kept on disk as the record of what was tried).
assets/bg_herringbone.png is a small (48px) seamless tile: neutral gray
strokes at low alpha (34/255), same "no hardcoded hex, works in both
themes automatically" trick as every palette()-based rule elsewhere in
this file -- alpha-blended mid-gray reads darker against a light
background and lighter against a dark one without needing separate
light/dark assets. Applied via QMainWindow's border-image (NOT
background-image -- see the comment right above the QMainWindow rule
below for why: background-repeat silently doesn't tile in Qt's QSS
engine, confirmed empirically, RUNNING.md #44 has the full story). See
_texture_asset_path below for why this can't just be a relative path or
Path(__file__)-relative -- same py2app-freezing wrinkle
app_logic.default_sample_image_path already had to solve, duplicated
here rather than shared since the two live in different modules with
no existing dependency between them.

Known limitation, not solved here: this is a single-density asset, no
@2x variant, so it may look softer/blurrier than the rest of the crisp
vector-drawn QSS chrome on a real Retina display -- genuinely unverified
until Tom looks at it on real hardware (see RUNNING.md).

FIFTH PASS (grain on panel separators, 2026-07-24) -- Tom's follow-up:
keep the herringbone background, add the "grain" candidate (the other
option from the original five, see gen_textures.py) specifically on
QMainWindow::separator -- the draggable dividers between docked panels,
a real, documented Qt subcontrol distinct from QFrame#panelFrame's own
static border. assets/border_grain.png reuses the exact density (22%)
and alpha range (8-26/255) as the original grain preview Tom already
saw, at the original 32px tile size (not bumped up like the herringbone
asset was -- a separator strip is only a few px thick, so extra tile
density wouldn't be visible either way). Same border-image (not
background-image) approach as the herringbone rule, for the same tiling
reason.

SEVENTH PASS (rounded corners everywhere, 2026-07-25) -- Tom's follow-up
after the menu-button/separator cleanup (RUNNING.md #52): the app still
read as hard-edged overall. Root cause -- QFrame#panelFrame already had
PANEL_BORDER_RADIUS, but every DOCKED panel's title actually lives in a
completely separate sibling widget (QWidget#panelTitleRow, handed to
QDockWidget.setTitleBarWidget() -- see main_window.py's
_build_dock_title_bar) sitting ABOVE panelFrame, not inside it (every
live PanelFrame() call passes no title at all -- the docking migration
moved the title out; PanelFrame's own title-row capability was later
removed entirely once confirmed dead, 2026-07-25, see panel_frame.py's
own docstring). panelTitleRow
previously had only a border-bottom divider line, no left/right/top
border and no radius at all, so each panel actually read as "a plain
underlined text strip sitting on top of a rounded box" rather than one
cohesive rounded shape -- the rounding on panelFrame alone was never
going to fix what actually reads as "hard edges" since the header (the
most visually prominent part of each panel) had none. Rather than try to
seam panelTitleRow and panelFrame into one continuous outline (they
already sit `margin-bottom: 4px` apart, a deliberate gap from the
title-bleed-texture fix in RUNNING.md #46 -- closing that gap to merge
the shapes would risk reopening that bug's conditions), panelTitleRow
now gets the exact same full border + border-radius treatment as
panelFrame, so header and body each read as their own clean rounded
piece, stacked -- same visual language, no seam-matching risk. Also
rounded in this pass: QListWidget (every library/browser list -- these
had a background-color but no border/radius at all, hard native
rectangular frame underneath), QMenu/QComboBox's popup view/QToolTip
(dropdowns and tooltips, hard-cornered before), and
QMainWindow::separator (a light border-radius even on a thin drag
handle, for consistency). NOT changed: QMainWindow's own OUTER window
corners -- that's the OS's native window chrome (title bar, traffic-
light buttons), not paintable via QSS at all; no stylesheet can round an
actual top-level window's corners short of Qt.WA_TranslucentBackground +
hand-painting the whole window frame, out of scope for a QSS pass and
not what Tom's asking about here (screenshot in question showed panel
chrome, not the outer app window).

NINTH PASS (fixed dark palette, no longer OS-theme-dependent, 2026-07-25)
-- reverses this file's original design call. Every rule above still
routes through `palette(...)` QSS functions rather than hardcoded hex --
that part is UNCHANGED -- but until this pass, `palette(...)` resolved
against whatever QPalette the OS handed QApplication, meaning a light
system theme produced a light app, which Tom saw directly (real
screenshots) and confirmed was bad: washed-out panel chrome, poor
contrast, nothing like the intentionally dark, low-glare tool this was
actually designed to look like (see this file's own SECOND PASS comment
-- "a colorist is very likely running a dark OS theme in a dim room" --
true, but Tom's ask now is stronger: don't just ACCOMMODATE a dark OS
theme, don't depend on OS theme at all).

Fix: `build_dark_palette()` below constructs a real `QPalette` with
FIXED colors (module-level `_DARK_*`/`_ACCENT_*` constants, the actual
easy knobs now if the exact grays or blue ever need adjusting) and
`main_window.py`'s `main()` calls `app.setPalette(build_dark_palette())`
right after `app.setStyle("Fusion")` and before `app.setStyleSheet(
STYLESHEET)` -- once that's set, every `palette(...)` reference in every
rule below (and every unstyled native widget/dialog Qt draws using the
app's palette directly, e.g. QMessageBox, QFileDialog, QColorDialog)
resolves against OUR fixed values, not the OS's, regardless of what
light/dark setting the OS is actually in. This is deliberately a
ONE-LINE-OF-CALLING-CODE fix rather than a rewrite of every rule in this
file to hardcoded hex -- the existing palette()-based architecture (one
role name reused consistently everywhere -- `mid` for borders AND muted
text, `highlight` for accent AND selection AND focus, etc.) is exactly
right for a fixed theme too, it just needed a fixed source of truth
instead of the OS.

Blue accent for panel headers, per Tom's explicit ask ("keep the
multiple grays etc, add blue accents for panel headers") -- panel
headers (`#panelTitleRow`/`#panelTitle`, see the "Panel frames" section
below) are the one place style.py now deliberately breaks from the
"everything through palette(text)/palette(mid)" floor: the title label's
color and the row's bottom border both switch to `palette(highlight)`
(the same blue `_ACCENT_BLUE` role used for selection/focus everywhere
else), so every panel's name reads in blue against the grays, a
consistent accent across all ten panels rather than a one-off hardcoded
color on just that rule.

`palette(link)` (Stack Forking's Branch B accent, distinct from Branch
A's `palette(highlight)`) is now `_ACCENT_AMBER` in `build_dark_palette`
-- deliberately NOT blue, since blue is the header/selection accent now;
Branch A/B need to stay visually distinct from each other, which
wouldn't hold if both resolved to the same color.

Known limitation, not solved here: this only affects colors that
already flowed through `palette(...)` -- the two texture assets
(`bg_herringbone.png`/`border_grain.png`, see below) are pre-rendered
alpha-blended greyscale PNGs, unaffected by QPalette, and were already
designed to read correctly against a dark background specifically (per
Tom, the FOURTH/FIFTH PASS notes above) -- no change needed there.
Native OS color pickers (QColorDialog, if this app ever adds one) and
some OS-chrome elements (an actual window titlebar/traffic-light
buttons) are the OS's own native chrome, not paintable via QPalette or
QSS at all -- same category of exception SEVENTH PASS's note above
already flagged for outer-window corners.

TENTH PASS (color correction + a real hint-text role, 2026-07-25) --
Tom's direct follow-up after seeing NINTH PASS: "valuea are a little off
from what id want. the panel's gray should be darker. (#3D3D3D) and the
accent should be darker and more muted (#2C4066) Make hint text
(#617191)". Three concrete changes: `_DARK_BASE` (panel/card bodies) is
now `#3D3D3D`; `_ACCENT_BLUE` (selection/focus/panel-header accent) is
now the darker, more muted `#2C4066`, not the brighter blue NINTH PASS
shipped; and a genuinely NEW color, `_HINT_TEXT` (`#617191`), for muted
INFORMATIONAL text specifically -- split out from `_DARK_MID`, which
until now did double duty as both "structural border/divider color" AND
"muted text color." `_DARK_MID` keeps the border/divider job and also
keeps coloring plain iconographic glyphs (the Stack stage row's ●/○
enabled-toggle and × delete button, muted-until-hover) -- `_HINT_TEXT`
(mapped onto `QPalette.PlaceholderText`, referenced in QSS as
`palette(placeholder-text)`) now covers every place this file or the
panel modules color actual PROSE dim/secondary/disabled text: hint
labels ("Select a stage above...", "Import a sample image...", Stack
Forking's explanatory text, opacity/domain readouts), disabled
QPushButton/QMenu-item text, and a disabled Stack stage's name label
(both the plain and disabled+selected variants). The Disabled QPalette
group's text roles (WindowText/Text/ButtonText, for any native widget
this app doesn't explicitly `:disabled`-style) also moved from
`_DARK_MID` to `_HINT_TEXT` for the same reason.

`_DARK_LIGHT`/`_DARK_MIDLIGHT` (hover states) were bumped up slightly
from their NINTH PASS values -- not something Tom asked for directly,
but a necessary side effect: both were tuned to sit just above the OLD,
darker `_DARK_BASE`; against the NEW, lighter `#3D3D3D`, the old
`_DARK_MIDLIGHT` in particular (`#3a3a3f`) had gone slightly DARKER than
the base it's meant to hover-lift away from, which would have made list-
row hover look like a dimming instead of a highlight. Nothing else was
touched -- `_DARK_WINDOW`, `_DARK_ALT_BASE`, `_DARK_BUTTON`, `_DARK_MID`,
`_DARK_DARK` are all unchanged from NINTH PASS.

TWELFTH PASS (2026-07-25) -- Tom's direct follow-up after #64's
diagnostic (which confirmed the Viewer panel's empty space was already
numerically `#3D3D3D`, not a bug): "Maybe its just my eyes. try this
gray #2B2B2B". `_DARK_BASE` is now `#2B2B2B` -- darker than TENTH PASS's
`#3D3D3D`, though still lighter than `_DARK_WINDOW` (`#242427`, the
space BETWEEN panels), so the "cards floating on a background" language
this file's own Two-tiers-of-gray comment above describes still holds,
just with a narrower gap between the two tiers than before. `_DARK_LIGHT`/
`_DARK_MIDLIGHT` (hover states) did NOT need re-tuning this time -- unlike
TENTH PASS, base got DARKER, not lighter, so the existing hover values
(already tuned to sit above the lighter `#3D3D3D`) sit even more clearly
above the new, darker base -- the hover-lift direction still reads
correctly with no adjustment. Nothing else touched.

THIRTEENTH PASS (2026-07-25) -- two more of Tom's follow-ups, same
message: "lets go even darker #242424. Also lets make the Panel header
text the same color as the hint text, but leave the accent on the bar."
`_DARK_BASE` is now `#242424` (see its own inline comment below for the
"this is now nearly the same as `_DARK_WINDOW`" flag -- applied as
asked, just noted for visibility, not second-guessed). `QLabel#panelTitle`
(the "Browser"/"Viewer"/etc. label text on every dock's title bar) now
uses `palette(placeholder-text)` (the same `_HINT_TEXT` role hint labels
and disabled text already use) instead of `palette(highlight)` --
`QWidget#panelTitleRow`'s border-bottom stays `palette(highlight)` at 2px,
untouched, so the accent is still there, just as an underline instead of
also coloring the label text itself.

FOURTEENTH PASS (real bug found while implementing the above, 2026-07-25)
-- while verifying THIRTEENTH PASS's new `QLabel#panelTitle` rule with an
actual pixel sample (not just "the stylesheet applied without error"),
found the rendered text was `#e6e6e9` (`_TEXT_PRIMARY`), not `#617191`
(`_HINT_TEXT`) -- and checking further, EVERY existing `palette(
placeholder-text)` usage in this app (the hint labels this file's own
TENTH PASS added, disabled button/menu text, the panel-module labels in
scopes_panel.py/aces_drt_panel.py/stack_panel.py/lut_controls_panel.py)
renders the same wrong way. TENTH PASS's own verification claimed these
all "read in the new muted blue-gray" -- that was a real miss, an
eyeballed screenshot read, not a pixel-sampled one; this pass is what
actually caught it.

Root cause, confirmed empirically (isolated repro, not guessed):
`QApplication.palette().color(PlaceholderText)` correctly holds
`_HINT_TEXT` for the app's entire lifetime -- but `MainWindow.palette()`
(and, it turns out, whatever the QSS engine actually paints from) drops
back to the Text color specifically for `PlaceholderText`, specifically
once `.show()` triggers Qt's style-polish pass, and specifically ONLY
for that one role -- `Base`/`Highlight`/`Text`/`Mid`/`Window`/`Light`/
`Midlight` were all checked side-by-side the same way and every one of
them survives polish correctly, confirmed unaffected by which platform
plugin renders (reproduced identically under both `offscreen` and
`minimal`, ruling out a sandbox-only artifact). `QPalette::PlaceholderText`
is Qt's real, narrower-purpose role for a QLineEdit/QTextEdit's actual
native placeholder-text rendering -- reusing it as a general "muted
prose text" role for arbitrary QLabel/QPushButton/QMenu text (TENTH
PASS's choice) was borrowing a role whose resolve/inheritance behavior
apparently isn't guaranteed for that use, at least on this Qt/PySide6
build (6.8.0.2).

Fix: stopped routing hint-text color through `palette(placeholder-text)`
entirely for prose/chrome text. `HINT_TEXT_HEX` (public alias for
`_HINT_TEXT`, defined right after it below) is the new single source of
truth -- every `color: palette(placeholder-text);` rule in this file's
STYLESHEET below is now a literal `color: {_HINT_TEXT}` (an f-string
interpolation, same mechanism `PANEL_BORDER_RADIUS`/the texture paths
already use elsewhere in this file -- not a new pattern), and every
panel module's own inline `setStyleSheet("color: palette(placeholder-
text)...")` call now imports `HINT_TEXT_HEX` from this module and
interpolates it the same way. `QPalette.ColorRole.PlaceholderText`
itself is LEFT mapped to `_HINT_TEXT` in `build_dark_palette()` below,
unchanged -- that's still correct for actual native placeholder text
(e.g. the Browser panel's search box), a genuinely different, working
mechanism from the broken QSS indirection this pass removes.

Verified by pixel-sampling (not just re-reading the QSS) after the fix:
the panel title text and every hint/disabled label now render exactly
`#617191`, confirmed via `win.grab()` + per-pixel color scan, not just a
visual glance. Full suite: 307 passed, same 19 pre-existing sandbox
failures, no regressions (paint-only).

FIFTEENTH PASS (2026-07-25) -- four more of Tom's color asks in one
message: panel gray darker still (`_DARK_BASE` now `#141414`, was
`#242424` -- see that constant's own comment for the "this now inverts
the panel-vs-window lightness relationship" flag), panel header text
`#BDBDBD`, hint text also `#BDBDBD` (so `_HINT_TEXT` itself just becomes
that value -- `QLabel#panelTitle` already routes through `_HINT_TEXT`
since THIRTEENTH/FOURTEENTH PASS, so the header-text ask lands for free,
no rule needs touching), and the bars between panels (`QMainWindow::
separator`) `#0D0D0D` via a new dedicated `_SEPARATOR_DIVIDER` constant
(not reusing `_DARK_MID`, which also colors ordinary structural borders
that weren't asked to change). Verified the same pixel-sampled way as
FOURTEENTH PASS caught its bug with -- not just re-reading the QSS."""

import os
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette

# Qt-free -- see theme_prefs.py's own docstring on why this is a separate
# module from workspace_io.py's panel-layout persistence. No circularity
# risk: theme_prefs -> workspace_io -> (json, pathlib) only, neither
# imports style.py back.
import theme_prefs

PANEL_BORDER_RADIUS = 6

# -- Fixed dark palette (NINTH PASS, see this file's module docstring) ----
# The actual easy knobs for the overall look now -- everything below is
# still reached exclusively through QPalette color ROLES (Window, Base,
# Text, Highlight, etc.), same as before this pass; these constants are
# just what those roles resolve to now, instead of the OS's own values.
# Two tiers of gray (window vs. base) is deliberate, not arbitrary --
# panels (`palette(base)`) need to read as a shade LIGHTER than the
# space between them (`palette(window)`, see QMainWindow's own rule
# below) for the existing "cards floating on a background" language
# (borders + radius, SEVENTH PASS) to still make sense once it's a fixed
# color instead of whatever the OS happened to hand over.
_DARK_WINDOW = "#242427"       # QMainWindow's own background, between panels
_DARK_BASE = "#141414"         # panel/card bodies, list/input backgrounds (per Tom,
                               # FIFTEENTH PASS -- was #242424). NOTE: this now
                               # INVERTS the "Two tiers of gray" relationship the
                               # comment right above describes -- panels
                               # (20,20,20) are now DARKER than the space between
                               # them (36,36,39), not lighter. The border+radius
                               # "cards" language still gives each panel a
                               # visible edge either way, just reading as a
                               # recessed/inset panel now rather than a raised
                               # one. Applied exactly as asked, flagged for
                               # visibility, not second-guessed -- see also
                               # _SEPARATOR_DIVIDER below, which is deliberately
                               # darker again than THIS, for a third, darkest
                               # tier at the actual bars between panels.
_DARK_ALT_BASE = "#343438"     # alternating rows, subtle raised surfaces
_DARK_BUTTON = "#38383c"       # button fill at rest
_DARK_LIGHT = "#4a4a4f"        # hover state -- bumped from the first pass so
                               # it's still clearly LIGHTER than the new,
                               # lighter _DARK_BASE above (hover has to read
                               # as "lifted," not just barely different)
_DARK_MIDLIGHT = "#47474c"     # list-row hover -- same reason as _DARK_LIGHT:
                               # must stay lighter than _DARK_BASE for the
                               # hover-lift direction to still make sense
_DARK_MID = "#4d4d53"          # borders, dividers, disabled-glyph color
_DARK_DARK = "#1a1a1c"         # pressed-button state, deepest shadow tone
_SEPARATOR_DIVIDER = "#0D0D0D" # FIFTEENTH PASS -- the actual bars BETWEEN
                               # docked panels (QMainWindow::separator, the
                               # draggable splitter handles), per Tom. A new,
                               # dedicated constant rather than repointing
                               # _DARK_MID (which also colors ordinary
                               # structural borders all over the app --
                               # panelFrame's outline, QLineEdit/QListWidget/
                               # QMenu borders, etc. -- changing THOSE wasn't
                               # asked for and shouldn't move just because the
                               # separator bars did). Not routed through
                               # QPalette at all -- literal hex in the QSS
                               # rule below, same reasoning as `_HINT_TEXT`'s
                               # FOURTEENTH PASS fix: this app already has one
                               # proven case (`PlaceholderText`) of a QPalette
                               # role not reliably surviving Qt's style-polish
                               # pass on this Qt build, so a color with no
                               # other consumer doesn't need that risk at all.
_TEXT_PRIMARY = "#e6e6e9"      # primary text -- off-white, not pure white
_ACCENT_BLUE = "#2C4066"       # DEFAULT/fallback accent (first launch, or
                               # theme_prefs.json missing/unreadable) --
                               # darker + more muted per Tom's second
                               # pass. SIXTEENTH PASS (2026-07-25): no
                               # longer THE accent unconditionally -- see
                               # DEFAULT_ACCENT_HEX/get_accent_color/
                               # set_accent_color below, "USER-CHOSABLE
                               # ACCENT COLOR."
_ACCENT_BLUE_TEXT = "#ffffff"  # DEFAULT contrast text for the default
                               # accent above -- see _contrast_text_color
                               # below for why this isn't unconditionally
                               # right anymore once the accent itself is
                               # user-chosen.
_ACCENT_AMBER = "#e0a542"      # Stack Forking Branch B -- must differ from
                               # the blue accent (Branch A) or the two
                               # branches stop being visually distinguishable
_HINT_TEXT = "#BDBDBD"         # muted/secondary INFORMATIONAL text (hint
                               # labels, disabled stage/button/menu TEXT) --
                               # FIFTEENTH PASS -- was #617191, now a plain
                               # light gray instead of a blue-tinted one (per
                               # Tom: "Hint text should also be #BDBDBD").
                               # Also the panel title/header text color,
                               # unchanged from THIRTEENTH PASS's choice to
                               # route `QLabel#panelTitle` through this same
                               # constant -- Tom's separate "make the panel
                               # name #BDBDBD" ask this pass lands on that
                               # rule for free, no code change needed there.
                               # Split out from _DARK_MID, which stays
                               # reserved for structural borders and
                               # icon-glyph colors (see PlaceholderText's
                               # mapping below, and every color-only, non-
                               # border TEXT rule that reads from HINT_TEXT_HEX
                               # -- see FOURTEENTH PASS for why this is a
                               # literal hex, not `palette(placeholder-text)`)

# FOURTEENTH PASS -- public (no leading underscore) alias for _HINT_TEXT,
# so panel modules that set hint-text color via their own setStyleSheet()
# calls (scopes_panel.py, aces_drt_panel.py, stack_panel.py,
# lut_controls_panel.py) can import ONE real source of truth instead of
# duplicating the hex literal -- see this file's own FOURTEENTH PASS
# docstring note for why they can no longer use `palette(placeholder-
# text)` for this like every other palette()-driven color in this file.
HINT_TEXT_HEX = _HINT_TEXT

# SIXTEENTH PASS -- USER-CHOSABLE ACCENT COLOR (2026-07-25, per Tom: "give
# users control over the accent color"). `DEFAULT_ACCENT_HEX` is the
# fallback (first launch, or theme_prefs.json missing/unreadable) -- same
# value `_ACCENT_BLUE` always was.
DEFAULT_ACCENT_HEX = _ACCENT_BLUE

# `get_accent_color()`/`set_accent_color()`
# below are the ONLY things that read/write the actual current accent
# from here on; `build_dark_palette()` calls `get_accent_color()` itself
# now instead of referencing `_ACCENT_BLUE` directly, so main_window.py's
# `main()` needs no change at all to pick up a saved color at startup.
#
# Cached in `_accent_color_cache` (module-level, lazily populated from
# theme_prefs.json on first read) rather than hitting disk on every
# `build_dark_palette()` call -- `build_dark_palette()` itself is cheap,
# but there's no reason to make it do a filesystem read every single
# call when the value only ever changes via `set_accent_color()`, which
# already knows to update the cache directly.
_accent_color_cache = None


def get_accent_color():
    """The current accent color hex string -- whatever the user last
    picked via `set_accent_color()` (this session or a previous one,
    persisted to theme_prefs.json), or `DEFAULT_ACCENT_HEX` if nothing's
    been chosen yet."""
    global _accent_color_cache
    if _accent_color_cache is None:
        _accent_color_cache = theme_prefs.load_accent_color() or DEFAULT_ACCENT_HEX
    return _accent_color_cache


def set_accent_color(hex_color):
    """Persist `hex_color` (a "#RRGGBB" string, e.g. from QColorDialog's
    `QColor.name()`) as the new accent, and update the in-memory cache so
    the very next `get_accent_color()`/`build_dark_palette()` call sees
    it immediately -- does NOT re-apply the palette itself; the caller
    (main_window._choose_accent_color) still needs to call
    `app.setPalette(build_dark_palette())` afterward for the change to
    actually show up on screen, same as every other palette rebuild in
    this app already does. Raises ValueError on anything that doesn't
    look like a real "#RRGGBB" hex string -- this is a direct user input
    path (a color picker), not an internal call with an
    already-known-good value, so it's worth validating rather than
    silently persisting garbage that'd only surface as a confusing QColor
    failure much later, on next launch."""
    if (not isinstance(hex_color, str) or len(hex_color) != 7
            or hex_color[0] != "#" or QColor(hex_color).name() != hex_color.lower()):
        raise ValueError(f"Expected a '#rrggbb' hex string, got {hex_color!r}")
    global _accent_color_cache
    _accent_color_cache = hex_color
    theme_prefs.save_accent_color(hex_color)


def get_tertiary_color():
    """A darker (33%) variant of `get_accent_color()` -- for a cue that
    should read as "related to the accent" without being confusable with
    the accent itself, e.g. QFrame#stackStageRow:hover in the STYLESHEET
    below (per Tom, 2026-08-12: hovering a Stack row needs its own color,
    distinct from BOTH the plain idle border and the full-accent
    selected-row border -- an unhovered, unselected row already uses
    palette(mid), so a third state needed a third color). Fulfilled
    task #23 ("derive a tertiary color automatically from the accent
    color, 33% darker") -- exposed as its own function, mirroring
    get_accent_color(), rather than inlined only where first needed,
    since any other "accent-adjacent but not THE accent" cue can reuse
    it. QColor.darker(150) is Qt's own way of saying "33% darker" --
    darker(factor) scales brightness by 100/factor, and 100/150 ~= 0.667
    (66.7% of original brightness = 33% darker), not a naive `factor=133`
    (that's only 25% darker; Qt's darker()/lighter() scale is inverted
    from a plain percentage-off reading)."""
    return QColor(get_accent_color()).darker(150).name()


def _contrast_text_color(hex_color):
    """Black or white, whichever reads better on top of `hex_color` --
    standard relative-luminance heuristic (ITU-R BT.601 coefficients,
    the same ones QColor.lightness() itself doesn't quite use but every
    "pick readable text for an arbitrary background" recipe converges
    on). Needed once the accent became user-choosable: `_ACCENT_BLUE_TEXT`
    (`#ffffff`) was a safe hardcode when the accent was always a fixed
    dark navy, but a user picking a light/pastel accent would get white
    text on a light background -- unreadable -- without this. Used for
    both `HighlightedText` (selected-row/selected-item text) and
    `BrightText` (drawn on top of the accent elsewhere) in
    `build_dark_palette()` below, same as `_ACCENT_BLUE_TEXT` covered
    both before this pass."""
    color = QColor(hex_color)
    luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255.0
    # 0.5, not some higher "only flip for very obviously light colors"
    # threshold -- checked against pure #00FF00 (green) while implementing
    # this: its luminance is ~0.587, clearly on the "use black text" side
    # perceptually (it's a bright, light-reading color), and a threshold
    # any higher than ~0.5-0.55 would have called that "dark enough for
    # white text," which is visibly wrong.
    return "#000000" if luminance > 0.5 else "#ffffff"


def build_dark_palette():
    """Fixed-color QPalette, deliberately NOT derived from the OS's own
    palette (see this file's NINTH PASS docstring for why) -- call
    `QApplication.setPalette(build_dark_palette())` before applying
    STYLESHEET (see main_window.py's main()) so every `palette(...)` QSS
    reference below, and every native/unstyled Qt widget this app doesn't
    explicitly style (QMessageBox, QFileDialog, native menus), draws from
    these fixed values regardless of the OS's actual light/dark setting.

    Sets both the Active and Inactive color groups to the identical fixed
    colors (a background app losing focus shouldn't suddenly look like a
    different theme), and dims the Disabled group's text roles to
    _HINT_TEXT (TENTH PASS -- was _DARK_MID) so a disabled control still
    reads as muted/unclickable the same way the existing `:disabled` QSS
    rules already assume, without needing every single disabled state
    hand-covered by QSS itself.

    SIXTEENTH PASS -- Highlight/BrightText/HighlightedText read from
    `get_accent_color()` (the user's saved choice, or `DEFAULT_ACCENT_HEX`)
    and `_contrast_text_color(...)` of that same value, NOT the fixed
    `_ACCENT_BLUE`/`_ACCENT_BLUE_TEXT` constants directly -- so calling
    this function again after `set_accent_color()` picks up the new
    accent, contrast-corrected, with no other change needed anywhere
    else in this file (every QSS `palette(highlight)` reference already
    resolves from whatever this returns).

    2026-08-12 -- same reasoning now also covers Accent, set from
    `get_tertiary_color()` (a 33%-darker derivative of the same accent),
    so `palette(accent)` in QSS reacts to accent changes the same
    automatic way `palette(highlight)` always has."""
    palette = QPalette()
    accent = get_accent_color()
    accent_text = _contrast_text_color(accent)

    active_and_inactive = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)
    role_colors = {
        QPalette.ColorRole.Window: _DARK_WINDOW,
        QPalette.ColorRole.WindowText: _TEXT_PRIMARY,
        QPalette.ColorRole.Base: _DARK_BASE,
        QPalette.ColorRole.AlternateBase: _DARK_ALT_BASE,
        QPalette.ColorRole.ToolTipBase: _DARK_BASE,
        QPalette.ColorRole.ToolTipText: _TEXT_PRIMARY,
        QPalette.ColorRole.Text: _TEXT_PRIMARY,
        QPalette.ColorRole.PlaceholderText: _HINT_TEXT,
        QPalette.ColorRole.Button: _DARK_BUTTON,
        QPalette.ColorRole.ButtonText: _TEXT_PRIMARY,
        QPalette.ColorRole.BrightText: accent_text,
        QPalette.ColorRole.Light: _DARK_LIGHT,
        QPalette.ColorRole.Midlight: _DARK_MIDLIGHT,
        QPalette.ColorRole.Mid: _DARK_MID,
        QPalette.ColorRole.Dark: _DARK_DARK,
        QPalette.ColorRole.Shadow: _DARK_DARK,
        QPalette.ColorRole.Highlight: accent,
        QPalette.ColorRole.HighlightedText: accent_text,
        QPalette.ColorRole.Link: _ACCENT_AMBER,
        QPalette.ColorRole.LinkVisited: _ACCENT_AMBER,
        # Every other classic QPalette role is already spoken for above
        # (see get_tertiary_color()'s own docstring) -- Accent is a
        # dedicated, Qt 6.6+ role this codebase had never used before,
        # confirmed via a direct QSS render test that `palette(accent)`
        # resolves correctly on the Qt 6.11 this app actually runs on.
        QPalette.ColorRole.Accent: get_tertiary_color(),
    }
    for group in active_and_inactive:
        for role, color in role_colors.items():
            palette.setColor(group, role, QColor(color))

    # Disabled group -- text roles dimmed to _HINT_TEXT (matches the
    # existing `:disabled` QSS rules' own color choice, see
    # QPushButton:disabled below), backgrounds left the same as
    # active/inactive since this app's :disabled QSS rules already
    # explicitly set backgrounds themselves where it matters.
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(_HINT_TEXT))

    return palette


def _texture_asset_path(filename):
    """Resolve an assets/ file the same way app_logic.default_sample_image_path
    does, and for the identical reason: py2app zips this module's .pyc into
    Contents/Resources/lib/python3XX.zip for a frozen build, so __file__'s
    parent at runtime is the zip archive itself, not Contents/Resources
    where DATA_FILES actually puts assets/ (see setup.py). Anchoring on
    RESOURCEPATH (set by py2app's __boot__) instead of __file__ when frozen
    sidesteps that. Returns a Path that may not exist; STYLESHEET below
    only uses this for a background-image url(), which just silently fails
    to paint if the file's missing -- no crash risk either way."""
    if getattr(sys, "frozen", False) and "RESOURCEPATH" in os.environ:
        base = Path(os.environ["RESOURCEPATH"])
    else:
        base = Path(__file__).parent
    return base / "assets" / filename


_HERRINGBONE_TEXTURE = _texture_asset_path("bg_herringbone.png").as_posix()
_GRAIN_TEXTURE = _texture_asset_path("border_grain.png").as_posix()

STYLESHEET = f"""
/* -- Dark-mode safety net -------------------------------------------- */
/* Per Tom: a bunch of text was unreadable in dark mode. Root cause --
   once QApplication.setStyleSheet() is set AT ALL (see main()), Qt stops
   syncing a handful of widget kinds to the live/dark palette on their
   own: QToolTip and QComboBox's popup list are the classic offenders
   (they keep rendering with whatever their OWN default palette resolves
   to, independent of the app's actual current palette -- on a dark
   theme that's frequently dark-text-on-dark-background, which reads as
   "the text is just gone" rather than obviously wrong-colored). Every
   widget kind below gets an explicit palette-based rule so it can never
   silently fall back to an unstyled default once this stylesheet exists.
   QWidget itself gets a floor-level `color`, lowest priority in QSS
   terms -- any more specific rule (an object-name selector, or a
   widget's own setStyleSheet(), e.g. the palette(placeholder-text) hint
   labels all over this app -- see NINTH/TENTH PASS below) still wins
   over it. */

QWidget {{
    color: palette(text);
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 2px 4px;
}}

/* Previously these four had no :hover/:focus state at all -- looked
   identical whether idle, moused-over, or actively being typed into.
   :hover is a light nudge (border only); :focus goes to the full accent
   plus a 2px width so the currently-editing field is unambiguous even
   at a glance across a busy panel. */
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid palette(highlight);
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 2px solid palette(highlight);
    padding: 1px 3px;
}}

QComboBox QAbstractItemView {{
    background-color: palette(base);
    color: palette(text);
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
    outline: none;
    border: 1px solid palette(mid);
    border-radius: 4px;
}}

QListWidget {{
    background-color: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    border-radius: 4px;
}}

QListWidget::item {{
    padding: 2px 4px;
}}

/* New: previously a row gave zero feedback until the click that actually
   selected it. palette(midlight) reads as "about to be interactive"
   without competing with the stronger palette(highlight) selected state
   right below it. */
QListWidget::item:hover:!selected {{
    background-color: palette(midlight);
}}

QListWidget::item:selected {{
    background-color: palette(highlight);
    color: palette(highlighted-text);
}}

/* Stack panel's list ONLY (_StackListWidget#stackListWidget, stack_panel.py)
   -- REAL BUG found by Tom (2026-08-12): each row already draws its own
   rounded "pill" card with its own selection/hover cue (a border-color
   change, see QFrame#stackStageRow above), but the generic rules just
   above ALSO paint their own sharp-cornered highlight rectangle behind/
   around every item -- for every OTHER QListWidget in this app (Browser,
   LUT/CDL Library, Stack Memories) that's the intended, only selection
   cue, but here it was a second, competing, uglier highlight the whole
   time (Tom: "the large highlight filling the regular 4 sided polygon
   around the row's pill shaped entry"), unrelated to and pre-dating this
   round's border-color changes -- it just never got addressed before
   because round one's bolder border treatment didn't visually compete
   with it as obviously. Transparent here, not removed outright, so the
   generic rules above stay exactly as they are for every other list. */
QListWidget#stackListWidget::item:selected {{
    background-color: transparent;
    color: palette(text);
}}

QListWidget#stackListWidget::item:hover:!selected {{
    background-color: transparent;
}}

/* Covers the new "..." header menus (Viewer/Stack, see panel_frame.py's
   corner_widget) as well as every QComboBox's own popup menu. */
QMenu {{
    background-color: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: palette(highlight);
    color: palette(highlighted-text);
}}

QMenu::item:disabled {{
    color: {_HINT_TEXT};
}}

QToolTip {{
    background-color: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 2px 4px;
}}

/* QDockWidget itself (see main_window.py's _make_dock --
   WA_StyledBackground set there for the same reason as this rule
   exists: QDockWidget is a plain QWidget under the hood, not a QFrame,
   so it needs the attribute for ANY QSS background to paint at all).
   EIGHTH PASS (2026-07-25, RUNNING.md #55) gave this palette(base), to
   stop QMainWindow's herringbone texture from bleeding through the
   small rectangular recesses where panelTitleRow/panelFrame's rounded
   corners used to pull back from the dock's own rectangular cell.

   TENTH PASS (2026-07-30, per Tom: "the only borderline [should] be one
   continuous one that encompasses everything including the panel
   header") -- this is now what draws THE outer border+radius for a
   whole panel, header and content together, instead of panelTitleRow
   and panelFrame each drawing their own separate full border+radius a
   few px apart (see those rules below for their half of this). Safe to
   merge now specifically because of the EIGHTH PASS fix above: the
   original worry (RUNNING.md #46's own comment, since removed) was that
   closing the gap between the two shapes might reopen the herringbone-
   bleed bug -- but that bug was about a missing OPAQUE background, and
   this rule has painted one across the dock's entire rectangle
   (underneath both children) since #55, well before this pass. Verified
   by screenshot after this change, same as every prior pass in this
   arc that touched this bleed-through-prone area. */
QDockWidget {{
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: {PANEL_BORDER_RADIUS}px;
}}

/* -- Panel frames (PanelFrame in panel_frame.py) -------------------- */

/* No border/full radius of its own anymore (TENTH PASS, see QDockWidget
   above) -- panelFrame is the BOTTOM piece of one shape now, so only
   its bottom two corners round, matching the dock's own outer bottom
   corners; its top edge sits flush against panelTitleRow with a plain
   square joint, not a corner at all. background-color stays: this is
   still the actual opaque backdrop content paints on. */
QFrame#panelFrame {{
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
    border-bottom-left-radius: {PANEL_BORDER_RADIUS}px;
    border-bottom-right-radius: {PANEL_BORDER_RADIUS}px;
    background-color: palette(base);
}}

/* TENTH PASS, per Tom: with panelFrame's own margin at zero (see that
   class's docstring), a panel's list widget -- always the sole,
   fully-stretched content in every current panel that has one (Browser,
   Image Library, LUT Library, CDL Library, Stack Memories) -- now sits
   directly against the outer QDockWidget border above, with nothing of
   panelFrame's own left. QListWidget's blanket border/radius rule
   further up this file is still right for a list widget used mid-panel
   next to other controls (none exist today, but nothing rules it out
   later) -- this narrower, more specific selector overrides it back to
   none ONLY for a list living inside a panelFrame, so there's exactly
   one border around the whole card, not two close together. Descendant
   selector (plain whitespace), not a direct-child one (">") -- the list
   widget is a grandchild of panelFrame (panelFrame -> the panel's own
   QWidget -> the QListWidget), never a direct child. */
QFrame#panelFrame QListWidget {{
    border: none;
    border-radius: 0px;
}}

/* Border/spacing live on the ROW (panelTitleRow), not the label itself --
   a corner_widget (e.g. Viewer/Stack's "..." menu, see panel_frame.py)
   sits on the same row as the title now, and the divider needs to span
   under both, not just the label's own text width.

   background-color is NEW as of RUNNING.md #46 -- real bug Tom caught on
   real hardware: this row never had an explicit background before, so it
   painted transparent and let whatever's behind it show through --
   harmless when that "whatever" was QMainWindow's old flat
   palette(window) color, but once #44 gave QMainWindow a herringbone
   border-image, every dock's title bar (this row IS a QDockWidget's
   setTitleBarWidget -- see main_window.py's _build_dock_title_bar) sat
   directly on top of it with nothing opaque in between, so the texture
   bled straight through. Still needed today even though QDockWidget
   itself also now paints an opaque background (see that rule above) --
   belt and suspenders, and this is what the label/corner_widget content
   actually paints against.

   NO full border/radius of its own anymore (TENTH PASS, per Tom -- see
   QDockWidget's own comment above for the one shared outer border this
   used to duplicate). Only the TOP two corners round -- this is the TOP
   piece of one shape now, its bottom edge sits flush against panelFrame
   with a plain square joint, and QDockWidget's own border already draws
   the left/right/top edges. border-bottom stays: the accent blue
   (palette(highlight), 2px) is a deliberate header/content divider, a
   different thing entirely from the outer card border this pass
   removed -- per Tom, panel headers specifically should carry it.
   margin-bottom is GONE too (used to be the 4px gap between this row and
   panelFrame, which is exactly the seam TENTH PASS closes). */
QWidget#panelTitleRow {{
    background-color: palette(base);
    padding: 4px 8px;
    border-bottom: 2px solid palette(highlight);
    border-top-left-radius: {PANEL_BORDER_RADIUS}px;
    border-top-right-radius: {PANEL_BORDER_RADIUS}px;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
}}

/* THIRTEENTH PASS -- title text is now palette(placeholder-text) (the
   same muted blue-gray as hint labels/disabled text, i.e. _HINT_TEXT),
   NOT palette(highlight) -- per Tom's "make the panel header text the
   same color as the hint text, but leave the accent on the bar." The
   accent stays -- see panelTitleRow's own border-bottom above, still
   palette(highlight) at 2px, untouched -- so each header still reads as
   accented via its underline, just without the label text itself being
   blue anymore. (Supersedes NINTH PASS's choice to color the title text
   blue too; that comment is left below for the historical "why blue was
   ever on the text" context, even though the color value itself has
   moved on.) */
QLabel#panelTitle {{
    font-weight: 600;
    font-size: 12px;
    color: {_HINT_TEXT};
}}

/* Close button on the dock title bar (see main_window.py's
   _build_dock_title_bar) -- flat/borderless at rest (autoRaise=True is
   set in code; this QSS just adds the accent hover state on top), same
   "reveals its own chrome on hover, not before" feel as
   QToolButton#stageDeleteButton below. Both this and panelMenuButton
   (below) are plain text-glyph buttons ("✕"/"☰") now, not QStyle
   standard-icon pixmaps -- per Tom, a pixmap icon next to a text glyph
   in the same row read as two different rendering styles side by side.
   Being plain text also means both ARE colorable via QSS `color` now,
   unlike a pixmap icon -- no override needed yet (default palette text
   color reads fine against panelTitleRow's background), but this is
   where one would go if that ever changes. */
/* panelMenuButton (the "☰" corner menu every panel builds via
   _build_menu_button/_build_list_menu_button) folded into this same
   rule per Tom -- it was left at Fusion's raw default QToolButton
   chrome (raised, bordered) since nothing styled it before, which read
   as visibly rougher than the flat close button right next to it.
   Menu-indicator arrow suppressed separately below -- setting a menu
   via setMenu() makes Fusion reserve space for and draw a small arrow
   regardless of ToolButtonPopupMode (InstantPopup here), so this button
   was showing BOTH "☰" and an arrow for the same single action; only
   these three buttons ever have a menu-indicator subcontrol to suppress
   at all, so the QToolButton::menu-indicator rule is safe as a blanket
   app-wide rule. */
QToolButton#dockCloseButton, QToolButton#panelMenuButton {{
    border: none;
    background: transparent;
    padding: 2px;
    min-width: 18px;
    min-height: 18px;
}}

QToolButton#dockCloseButton:hover, QToolButton#panelMenuButton:hover {{
    background-color: palette(highlight);
    border-radius: 3px;
}}

/* Suppresses the extra dropdown-arrow subcontrol Fusion draws for any
   QToolButton with setMenu() called on it, regardless of
   ToolButtonPopupMode -- see panelMenuButton's own comment above.
   image: none removes the arrow glyph; width: 0px reclaims the space
   Fusion otherwise reserves for it, so "☰" sits centered exactly like
   dockCloseButton's "✕" does, not off-center to make room for an
   arrow that's no longer there. */
QToolButton::menu-indicator {{
    image: none;
    width: 0px;
}}

/* Video transport bar buttons (sample_image_panel.py's Play/Pause, step,
   in/out, loop -- Phase 4 transport upgrade follow-up, per Tom: the raw
   Fusion default raised/bordered chrome these were left in read as
   visibly rougher than the rest of this panel's flat styling, the same
   gap panelMenuButton's own comment above already called out and fixed
   once). Same flat-at-rest, accent-highlight-on-hover shape as
   dockCloseButton/panelMenuButton, just a slightly wider min-width to
   fit two-glyph labels ("◀│"/"│▶") without clipping. The Loop button
   ALSO gets a persistent highlighted state while checked (native
   QToolButton :checked pseudo-state, no dynamic property needed,
   unlike stageEnabledToggle's true/false property below) -- it's a
   toggle, not a momentary action, so its state needs to stay visible
   without a cursor sitting on it. */
QToolButton#videoTransportButton {{
    border: none;
    background: transparent;
    padding: 2px;
    min-width: 22px;
    min-height: 20px;
}}

QToolButton#videoTransportButton:hover {{
    background-color: palette(highlight);
    border-radius: 3px;
}}

QToolButton#videoTransportButton:checked {{
    background-color: palette(highlight);
    border-radius: 3px;
}}

/* Browser panel's "Choose Folder..." button (per Tom: "a cleaner more
   in-app style button rather than a system button") -- was a plain
   QPushButton, so it got the raised/bordered chrome the base QPushButton
   rule above gives every ordinary action button; that's the "system
   button" look Tom was pointing at, same complaint already fixed the
   same way for panelMenuButton/videoTransportButton above. Swapped to a
   QToolButton (autoRaise=True in code) with the identical flat-at-rest,
   accent-highlight-on-hover treatment -- padding sized for its text
   label instead of those two's fixed icon-glyph min-width. */
QToolButton#chooseFolderButton {{
    border: none;
    background: transparent;
    padding: 4px 10px;
}}

QToolButton#chooseFolderButton:hover {{
    background-color: palette(highlight);
    border-radius: 4px;
}}

/* Real SMPTE timecode readout (Phase 4, timecode.py) -- monospace so
   digits don't jitter the label's own width as they change every tick
   during playback, same reason any numeric readout that updates in
   place wants a fixed-width face. */
QLabel#videoTimecodeLabel {{
    font-family: Menlo, Consolas, monospace;
}}

/* EDL sub-panel text (timeline_panel.py, per Tom: "add an EDL expression
   of the timeline in as a sub panel") -- monospace so the fixed-width
   CMX3600 event columns actually line up. */
QPlainTextEdit#edlTextView {{
    font-family: Menlo, Consolas, monospace;
    font-size: 11px;
    background-color: {_DARK_BASE};
    color: {_TEXT_PRIMARY};
    border: 1px solid {_SEPARATOR_DIVIDER};
}}

/* -- Buttons, all of them ------------------------------------------- */
/* Flat-at-rest, accent-highlight-on-hover -- same "in-app" treatment as
   dockCloseButton/panelMenuButton/videoTransportButton/
   chooseFolderButton above, per Tom: "remove all system style buttons
   and replace with buttons similar to Select Folder in media browser."
   QPushButton used to get its own bordered/filled "system button" look
   (palette(mid) border, palette(button) background) here, which is
   exactly the visual language those other buttons were already pulled
   OUT of -- every plain QPushButton in the app (Update Stage, Export...,
   Reset to 0/Identity, etc.) picks this rule up automatically via the
   bare `QPushButton` selector, no per-call-site changes needed. */

QPushButton {{
    padding: 4px 10px;
    border-radius: 4px;
    border: none;
    background: transparent;
    color: palette(button-text);
}}

QPushButton:hover {{
    background-color: palette(highlight);
}}

QPushButton:pressed {{
    background-color: palette(highlight);
}}

QPushButton:disabled {{
    color: {_HINT_TEXT};
    background: transparent;
}}

QPushButton:checkable:checked {{
    background-color: palette(highlight);
    color: palette(highlighted-text);
    font-weight: 600;
}}

/* -- Group boxes (used inside Controls) ------------------------------ */

QGroupBox {{
    border: none;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: 600;
    color: palette(text);
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: palette(highlight);
}}

/* -- Stack stage rows (StackPanel's _StageRowWidget, stack_panel.py) --- */
/* Each stage is its own bordered card now instead of one line of plain
   list text -- [stageSelected="true"] tracks the current row explicitly
   since a setItemWidget()-covered QListWidgetItem doesn't paint Qt's own
   selection highlight through the widget on top of it (see
   StackPanel._update_row_selection_styling). [stageEnabled="false"] dims
   a disabled stage's card so its on/off state reads at a glance, not just
   from the toggle glyph.

   SIMPLIFIED PER TOM (2026-08-12): selection used to also grow the
   border to 2px and recolor+bold the label text -- Tom's call after
   trying it was that the highlight should be JUST the border's own
   color changing to accent, nothing else (no width change -- so a row
   doesn't nudge its neighbors' vertical position on select/deselect --
   and no label recolor competing with [stageBranch]'s own accent).

   Hover went through three rounds before landing here. Round one used a
   subtler brightened-gray hover (palette(light)) -- effectively
   invisible, only 3 RGB values off palette(mid), confirmed by direct
   pixel sampling. Round two matched this app's text-field convention
   (QLineEdit/QComboBox/QSpinBox etc. above go straight to a full
   `palette(highlight)` border on :hover) -- but per Tom, that made a
   merely-hovered row look identical to an actually-SELECTED one, which
   was confusing rather than helpful once seen live. Round three (this
   one): hover gets its OWN color, `palette(accent)` -- see
   get_tertiary_color() in this file, a 33%-darker derivative of the
   accent, distinct from both the plain idle gray and the full-strength
   selected accent. A row that's BOTH selected and hovered still shows
   the full accent, not the darker tertiary -- same "the stronger state
   wins" precedent a focused-AND-hovered QLineEdit already follows
   elsewhere in this file. WA_Hover is set on _StageRowWidget in Python
   (QFrame doesn't opt into :hover style repaints on its own).

   Each state below restates the FULL border shorthand (width/style/
   color), not just a `border-color` override on top of the base rule --
   a `border-color`-only override wasn't reliably winning against the
   base rule's own full `border:` shorthand on this Qt build (found via
   Tom's testing: no visible change at all), so every state is now
   fully self-contained instead of depending on shorthand/longhand
   cascade behavior. */

QFrame#stackStageRow {{
    border: 1px solid palette(mid);
    border-radius: {PANEL_BORDER_RADIUS}px;
    background-color: palette(base);
    margin: 2px 0px;
}}

QFrame#stackStageRow:hover {{
    border: 1px solid palette(accent);
}}

QFrame#stackStageRow[stageSelected="true"] {{
    border: 1px solid palette(highlight);
}}

/* Explicit, not left to selector-specificity ordering -- a SELECTED row
   being hovered must stay at full accent strength, not demote to the
   darker hover-only tertiary color the plain :hover rule above uses. */
QFrame#stackStageRow[stageSelected="true"]:hover {{
    border: 1px solid palette(highlight);
}}

QFrame#stackStageRow[stageEnabled="false"] {{
    background-color: palette(window);
}}

QToolButton#stageEnabledToggle {{
    font-weight: 700;
    font-size: 13px;
    min-width: 18px;
}}

QToolButton#stageEnabledToggle[stageEnabled="true"] {{
    color: palette(highlight);
}}

QToolButton#stageEnabledToggle[stageEnabled="false"] {{
    color: palette(mid);
}}

QLabel#stageLabel[stageEnabled="false"] {{
    color: {_HINT_TEXT};
}}

/* Deliberately a different glyph/style from stageEnabledToggle's
   checkmark/cross (see _StageRowWidget) so the two aren't confusable --
   muted by default, palette(highlight) on hover as the only "this is
   about to do something" cue, no hardcoded red -- stays legible and
   consistent in dark mode same as everything else in this stylesheet. */
QToolButton#stageDeleteButton {{
    font-weight: 700;
    font-size: 13px;
    min-width: 18px;
    color: palette(mid);
}}

QToolButton#stageDeleteButton:hover {{
    color: palette(highlight);
}}

/* Kind badge (_stage_kind_badge_text, e.g. "CDL"/"GP"/"DRT") -- a quick
   visual "what is this row" cue on full-width rows, added per Tom's
   "snappier and more interactive" pass. A muted pill rather than an
   accent color -- it's a label, not a status indicator, so it shouldn't
   compete with the selection/branch accents above. */
QLabel#stageKindBadge {{
    font-size: 9px;
    font-weight: 700;
    color: palette(mid);
    background-color: palette(alternate-base);
    border-radius: 3px;
    padding: 1px 4px;
    min-width: 22px;
}}

/* Matte-channel letter badge (_StageRowWidget.matte_letter_badge, per
   Tom: "it needs to be clear what the letter code for each matte is")
   -- shown only on a matte-PRODUCING row (a "keyer"/"segment_mask"
   stage). Accent-colored, UNLIKE the muted kind badge just above --
   this one IS meant to read as "the thing to remember," since it's
   exactly what a downstream row's own matte_combo dropdown shows to
   match it up against. */
QLabel#stageMatteLetterBadge {{
    font-size: 10px;
    font-weight: 700;
    color: palette(highlighted-text);
    background-color: palette(accent);
    border-radius: 7px;
    padding: 1px 5px;
    min-width: 14px;
}}

/* Per-row opacity spin (_StageRowWidget.opacity_spin) -- moved in-row
   next to the stage name per Tom (2026-08-12 round two; the old
   "Opacity:" control lived below the whole list and only ever acted on
   whichever stage happened to be selected, which read as confusing).
   Smaller than the app's default QSpinBox styling (see the QSpinBox
   rule above) -- this one lives inside an already-busy row, not a
   dedicated form field. */
QSpinBox#stageOpacitySpin {{
    font-size: 10px;
    padding: 1px 2px;
}}

/* -- Stack Forking (fork_split/fork_merge rows, the branch table) ----- */
/* Per Tom's Stack Forking spec, second pass: fork_split/fork_merge get
   NO special styling at all now -- Tom's call after the first pass was
   that a dashed/tinted marker row looked "bulky and non-matching" next
   to ordinary operator cards, so they're plain QFrame#stackStageRow
   cards like everything else; the region between them is what reads as
   distinct now, via the two-column table (#stackForkGridRow /
   #stackForkEmptyCell, see _ForkBranchGridRowWidget/_EmptyBranchCell in
   stack_panel.py) rather than the split/merge rows themselves.
   [stageBranch="a"/"b"] still exists but is only ever applied to a STRAY
   branch tag -- a stage that carries a Branch A/B tag but isn't actually
   between a matched split/merge pair right now (see _StageRowWidget's
   docstring) -- since inside the table, column position already says
   which branch a cell belongs to and repeating it as a per-cell accent
   would be exactly the redundant decoration Tom's "bulky" note was
   about. palette(highlight) for A, palette(link) for B -- two different
   accent roles Qt already resolves sanely in both light and dark themes,
   same "no hardcoded hex" rule as the rest of this file. */

QFrame#stackStageRow[stageBranch="a"] {{
    border-left: 3px solid palette(highlight);
}}

QFrame#stackStageRow[stageBranch="b"] {{
    border-left: 3px solid palette(link);
}}

/* An empty placeholder cell in the branch table (see _EmptyBranchCell) --
   a faint dashed outline so it reads as "deliberately blank," not a
   layout glitch, without competing with real stage cards next to it. */
QFrame#stackForkEmptyCell {{
    border: 1px dashed palette(mid);
    border-radius: {PANEL_BORDER_RADIUS}px;
    background-color: transparent;
}}

/* -- Sliders (printer-lights/CDL-sat/fork-blend trims, graticule
   intensity, 3D grid density, cage opacity -- see lut_controls_panel.py,
   stack_panel.py, scopes_panel.py, lut_viewer_3d.py) ------------------ */
/* Previously fully native/unstyled. sub-page (the filled portion left of
   the handle) uses the accent so the slider reads as "this much is
   active," matching the same highlight language as everything else --
   handle stays a plain accent-filled circle, no separate hover state
   (kept intentionally simple for this "easy pass"). :disabled explicitly
   falls back to palette(mid) rather than relying on Qt to auto-dim a
   QSS-overridden color, same reasoning as QPushButton:disabled above. */

QSlider::groove:horizontal {{
    height: 4px;
    background: palette(mid);
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: palette(highlight);
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: palette(highlight);
    border: 1px solid palette(highlight);
    width: 14px;
    height: 14px;
    margin: -6px 0px;
    border-radius: 7px;
}}

QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{
    background: palette(mid);
    border-color: palette(mid);
}}

/* Video transport scrub bar (video_scrub_slider.VideoScrubSlider) --
   direct feedback on the general QSlider look above, scoped by ID so it
   doesn't touch every OTHER slider in the app (printer-lights/CDL-sat/
   fork-blend/graticule/grid-density/cage-opacity keep the general style):
   "the handle and highlight on the scrub is ugly ... instead of a knob
   there should be a simple white playhead." sub-page's accent "elapsed"
   fill is flattened to match the groove exactly (no elapsed/remaining
   distinction at all), and the handle is made fully invisible (0 width,
   transparent) -- VideoScrubSlider's own paintEvent draws a plain white
   line at the current value instead, plus thin in/out marker lines, see
   that class's own docstring. */
QSlider#videoScrubSlider::groove:horizontal {{
    height: 4px;
    background: palette(mid);
    border-radius: 2px;
}}

QSlider#videoScrubSlider::sub-page:horizontal {{
    background: palette(mid);
    border-radius: 2px;
}}

QSlider#videoScrubSlider::handle:horizontal {{
    width: 0px;
    height: 0px;
    margin: 0px;
    border: none;
    background: transparent;
}}

/* -- Scrollbars (implicit, via QListWidget/QTableWidget across every
   library/browser panel -- see browser_panel.py, lut_library_panel.py,
   media_panel.py, stack_memories_panel.py, stack_panel.py) ---- */
/* Had zero styling before -- whatever the native OS scrollbar looked
   like. Thin, minimal, transparent track; handle picks up the accent
   color on hover only, staying quiet (palette(mid)) at rest so it
   doesn't compete with list content. No arrow buttons (add-line/
   sub-line collapsed to 0px) -- matches the minimal-track look most
   modern list UIs use instead of classic up/down stepper buttons. */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: palette(mid);
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: palette(highlight);
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: palette(mid);
    border-radius: 4px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background: palette(highlight);
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

/* -- General spacing -------------------------------------------------- */

/* background-color stays palette(window) as the base layer (shows
   through the texture PNG's transparent pixels, and as a full fallback
   if the asset is ever missing -- see _texture_asset_path's docstring).
   Quoted url() since this path likely contains spaces (e.g.
   "Application Support" on macOS) -- Qt QSS's url() doesn't tolerate
   unquoted spaces.

   Deliberately `border-image`, NOT `background-image` +
   `background-repeat: repeat` -- verified empirically (screenshot-
   grabbed a bare QMainWindow and a bare QWidget headlessly) that Qt's
   `background-repeat: repeat` silently does NOT tile; the image paints
   once in the top-left corner and stops, `background-repeat` having no
   visible effect at all. `border-image` with explicit `repeat repeat`
   (horizontal, vertical) DOES tile correctly, confirmed the same way.
   The "0 0 0 0" is the border-image slice widths -- zero on all four
   sides so the whole image is the tiled "middle" region rather than
   being sliced into corners/edges the way border-image is normally used
   for 9-patch button chrome. */
QMainWindow {{
    background-color: palette(window);
    border-image: url("{_HERRINGBONE_TEXTURE}") 0 0 0 0 repeat repeat;
}}

/* QMainWindow::separator -- the real, documented Qt subcontrol for the
   draggable dividers BETWEEN docked panels (distinct from
   QFrame#panelFrame's own static border around each panel's contents).
   SIXTH PASS (2026-07-25) -- Tom's follow-up on seeing this on real
   hardware: the grain texture (FIFTH PASS, above) read as "rough edges"
   rather than a deliberate texture, so it's removed here -- flat
   palette(mid), no border-image. _GRAIN_TEXTURE/border_grain.png is
   left in place on disk and _GRAIN_TEXTURE's own definition is
   untouched (unused now, not deleted) in case a future pass wants a
   textured separator again with a lighter touch; only this rule's use
   of it is gone. Width/height kept at 6px -- that was never part of
   the "rough" complaint, still a reasonable drag-target size for a
   splitter handle.
   FIFTEENTH PASS (2026-07-25) -- per Tom, "the bars between panels"
   (this IS those bars) get their own dedicated, darker color now,
   `_SEPARATOR_DIVIDER` (`#0D0D0D`) -- a literal hex, not `palette(mid)`
   anymore, see that constant's own comment above for why. */
QMainWindow::separator {{
    background-color: {_SEPARATOR_DIVIDER};
    width: 6px;
    height: 6px;
    border-radius: 3px;
}}

/* Viewer SOURCE/RECORD mode TOGGLE (sample_image_panel.py's
   _set_viewer_mode/_on_viewer_mode_toggled, per direct feedback: "we
   need to ... easily tell if we are looking at the timeline or not",
   then a follow-up round: "you dont need that red for record. it's not
   like arming a recording in a DAW" -- RECORD reuses this app's
   existing palette(highlight) "this one's active/selected" language
   (same convention QToolButton#videoTransportButton:checked and
   QFrame#stackStageRow[stageSelected="true"] already use), not a
   special alarm color. SOURCE is the quiet, unchecked default. */
QFrame#viewerModeFrame[viewerMode="record"] {{
    border: 2px solid palette(highlight);
    border-radius: 4px;
}}
QFrame#viewerModeFrame[viewerMode="source"] {{
    border: 2px solid transparent;
    border-radius: 4px;
}}
/* #viewerModeBadge is a checkable QToolButton, not a QLabel -- its own
   RECORD/SOURCE look comes from the native :checked pseudo-state
   (toggled by _set_viewer_mode's setChecked call), not a dynamic
   property, same reasoning videoTransportButton:checked above already
   follows for the Loop button. */
QToolButton#viewerModeBadge {{
    font-size: 10px;
    font-weight: 700;
    border-radius: 3px;
    padding: 1px 6px;
    max-width: 60px;
    color: palette(mid);
    background-color: palette(alternate-base);
}}
QToolButton#viewerModeBadge:checked {{
    color: palette(highlighted-text);
    background-color: palette(highlight);
}}
"""
