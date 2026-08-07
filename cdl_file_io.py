"""
cdl_file_io.py -- import ASC CDL grades from real .cc/.cdl/.ccc files via
OpenColorIO's own CDLTransform.CreateGroupFromFile, which parses all three
container formats UNIFORMLY into the same GroupTransform of CDLTransform
objects, no extension-based branching needed:

  - .cc  -- a bare single <ColorCorrection>.
  - .cdl -- a <ColorDecisionList> wrapping one-or-more
    <ColorDecision>/<ColorCorrection> pairs.
  - .ccc -- a <ColorCorrectionCollection> with one-or-more bare
    <ColorCorrection> entries.

Confirmed directly against hand-written sample files of all three kinds
(see test_cdl_file_io.py) -- CreateGroupFromFile('single.cc') returns a
length-1 group exactly like CreateGroupFromFile('multi.ccc') returns a
length-N group, both iterable the same way.

Same "trust the vetted reference implementation" rule as aces_drt.py/
cdl.py -- no hand-rolled XML parsing. Reuses cdl.py's already-imported
PyOpenColorIO module and OCIO_AVAILABLE/OCIO_IMPORT_ERROR flags rather
than re-importing OCIO independently, same "tightly related sibling
module" reasoning colorspace_convert.py used for reusing aces_drt.py's
registry (this module and cdl.py are both fundamentally "ways to use
OCIO's CDLTransform," just reading vs. constructing one).

Returns plain dicts (id/description/slope/offset/power/sat/style), not
cdl_library.CDLEntry objects -- this module's only job is "read a file,
hand back what was in it," same integration point as
app_logic.load_lut_any_format returning a plain colour.LUT3D/LUT1D rather
than anything about the Stack or a Stage. cdl_library_panel.py is what
turns these dicts into CDLEntry objects (deciding names, recording
source_filename/source_path) and adds them to the library.
"""
import cdl

OCIO_AVAILABLE = cdl.OCIO_AVAILABLE
OCIO_IMPORT_ERROR = cdl.OCIO_IMPORT_ERROR

# The three ASC CDL container extensions this module (via OCIO) reads --
# same role app_logic.SUPPORTED_EXTENSIONS plays for LUT files. Used by
# browser_panel.py to recognize/scan for these alongside LUT files, and
# by cdl_library_panel.py for its Import... file dialog filter.
CDL_EXTENSIONS = {".cc", ".cdl", ".ccc"}


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so CDL file import is "
            f"unavailable (import error: {OCIO_IMPORT_ERROR}). "
            "Install it with: pip3 install opencolorio"
        )


def _style_from_ocio(ocio_style):
    """ocio.CDLStyle -> this app's "asc"/"no_clip" string convention (see
    cdl.py's AVAILABLE_STYLES) -- defaults to "no_clip" for any style this
    app doesn't have its own name for, rather than raising, since an
    unrecognized style shouldn't block importing the file's actual SOP/SAT
    values."""
    ocio_mod = cdl.ocio
    if ocio_style == ocio_mod.CDLStyle.CDL_ASC:
        return "asc"
    return "no_clip"


def parse_cdl_file(path):
    """Returns a list of dicts, one per ColorCorrection found in `path` (a
    .cc/.cdl/.ccc file) -- see module docstring for the three container
    formats, all handled the same way here. Each dict:
    {"id": str, "description": str, "slope": (r,g,b), "offset": (r,g,b),
    "power": (r,g,b), "sat": float, "style": "asc"|"no_clip"}.
    "id"/"description" come straight from the file's own
    <ColorCorrection id="..."> attribute / <SOPNode><Description>, either
    of which may be an empty string if the file didn't set them --
    cdl_library_panel.py is what decides on a fallback display name for
    that case, not this module.

    Raises whatever OCIO itself raises for a malformed/unreadable file --
    same "surface the real error rather than a vague wrapper" convention
    as app_logic.load_lut_any_format.
    """
    _require_ocio()
    ocio_mod = cdl.ocio
    group = ocio_mod.CDLTransform.CreateGroupFromFile(str(path))
    entries = []
    for transform in group:
        entries.append({
            "id": transform.getID(),
            "description": transform.getFirstSOPDescription(),
            "slope": tuple(transform.getSlope()),
            "offset": tuple(transform.getOffset()),
            "power": tuple(transform.getPower()),
            "sat": transform.getSat(),
            "style": _style_from_ocio(transform.getStyle()),
        })
    return entries
