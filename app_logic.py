"""
app_logic.py -- Qt-free backend for the GUI. Everything here is plain Python
plus colour-science/colour_clf_io/numpy, deliberately kept free of any
PySide6/pyqtgraph import so it can be fully tested without a display or a
working Qt install (this sandbox is missing a system library Qt6 needs even
to import -- see gui/README_ENVIRONMENT.md). The GUI layer should be a thin
wrapper calling into this module, not reimplementing any of this logic.
"""
import fnmatch
import io
from pathlib import Path

import numpy as np
import colour
import colour_clf_io as clf
from PIL import Image

from clf_adapter import bake_process_list_to_lut3d
import image_io
from retarget import retarget_io
from scopes import LUMA_COEFFICIENTS_REC709

# colour-science's native read_LUT covers these by extension. .clf is handled
# separately via colour_clf_io. .3dl (Autodesk/Lustre) is a known gap --
# neither library supports it yet; noted for later, not built.
NATIVE_EXTENSIONS = {".cube", ".spi1d", ".spi3d", ".spimtx", ".csp"}
CLF_EXTENSIONS = {".clf"}
SUPPORTED_EXTENSIONS = NATIVE_EXTENSIONS | CLF_EXTENSIONS


WILDCARD_CHARS = set("*?[]")


def _name_matches_filter(filename, name_filter):
    """Case-insensitive match against the browser search box.

    fnmatch.fnmatch anchors to the WHOLE string -- "rec709*p3" means
    "starts with rec709, ends with p3", full stop. That makes a
    part-then-wildcard-then-part search like "rec709*constrain709" fail
    on any real filename that has more after "constrain709" (an
    extension, a version suffix, anything), which is nearly always --
    only a trailing "*" happened to paper over this, since it makes "ends
    with anything" trivially true. Not what a search box should do.

    Fix: always treat the pattern as "contains this shape somewhere," not
    "is exactly this shape" -- auto-add a leading/trailing "*" whenever
    one isn't already there, for both the wildcard path and the plain
    substring fallback. So "rec709*constrain709" becomes
    "*rec709*constrain709*" and correctly matches
    "rec709_to_p3d65pq_constrain709_v173.clf" even though there's more
    filename after "constrain709". A search with no wildcard characters
    at all still behaves as plain "contains", same as before.
    """
    pattern = name_filter if WILDCARD_CHARS & set(name_filter) else name_filter
    if not pattern.startswith("*"):
        pattern = "*" + pattern
    if not pattern.endswith("*"):
        pattern = pattern + "*"
    return fnmatch.fnmatch(filename.lower(), pattern.lower())


def scan_lut_folder(folder, recursive=True, name_filter=None, extra_extensions=None):
    """Return a sorted list of Path objects for every recognized LUT file
    under `folder`. name_filter, if given, matches against the filename --
    see _name_matches_filter for the wildcard-vs-substring rule.

    extra_extensions: an optional extra set of suffixes (e.g.
    cdl_file_io.CDL_EXTENSIONS) to recognize ALONGSIDE SUPPORTED_EXTENSIONS,
    without changing what SUPPORTED_EXTENSIONS itself means elsewhere (a
    "lut" stage's loader, format export, etc. all still only care about
    real LUT formats) -- browser_panel.py uses this to list ASC CDL files
    next to LUT files in one scan, so callers that don't pass it (existing
    behavior, see test_browser_search.py) are entirely unaffected."""
    folder = Path(folder)
    pattern = "**/*" if recursive else "*"
    allowed = SUPPORTED_EXTENSIONS | (set(extra_extensions) if extra_extensions else set())
    results = []
    for path in folder.glob(pattern):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed:
            continue
        if name_filter and not _name_matches_filter(path.name, name_filter):
            continue
        results.append(path)
    return sorted(results, key=lambda p: p.name.lower())


def load_lut_any_format(path, clf_bake_size=33):
    """Load any supported LUT format into a colour.LUT1D/LUT3D-like object
    with a .apply(RGB) method and a .table attribute.

    CLF files may contain a chain of nodes (not just a single flat LUT), so
    they're baked to a flat LUT3D via bake_process_list_to_lut3d (which uses
    our own verified node execution, not colour_clf_io's buggy apply()).
    clf_bake_size controls the resolution of that bake -- lower is faster to
    load/visualize, higher is more accurate for actually exporting. 33 is a
    reasonable default for interactive use; use something like 65 for a
    final export-quality bake.
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext in CLF_EXTENSIONS:
        process_list = clf.read_clf_from_file(str(path))
        return bake_process_list_to_lut3d(process_list, size=clf_bake_size, name=path.stem)

    if ext in NATIVE_EXTENSIONS:
        result = colour.read_LUT(str(path))
        # LUTSequence (a chain, from formats that can express one) doesn't
        # have a flat .table -- bake it the same way we bake CLF chains, by
        # sampling its own .apply() on an identity grid.
        if isinstance(result, colour.LUTSequence):
            size = clf_bake_size
            identity_grid = colour.LUT3D(size=size).table
            baked_table = result.apply(identity_grid)
            return colour.LUT3D(table=baked_table, size=size, name=path.stem)
        return result

    raise ValueError(f"Unsupported LUT extension: {ext} ({path})")


def get_lut_info(lut, source_path=None):
    """Human-readable summary dict for display in the app."""
    info = {
        "name": getattr(lut, "name", None) or (Path(source_path).stem if source_path else "unnamed"),
        "type": type(lut).__name__,
        "source_path": str(source_path) if source_path else None,
    }
    table = getattr(lut, "table", None)
    if table is not None:
        info["dimensions"] = table.ndim - 1 if table.ndim > 1 else 1
        info["size"] = table.shape[0]
        info["value_range"] = (float(np.min(table)), float(np.max(table)))
    domain = getattr(lut, "domain", None)
    if domain is not None:
        info["domain"] = np.asarray(domain).tolist()
    comments = getattr(lut, "comments", None)
    if comments:
        info["comments"] = list(comments)
    return info


def extract_3d_points_for_visualization(lut, subsample_size=9):
    """Return (positions, colors) numpy arrays, both shape (N, 3), N =
    subsample_size**3, suitable for pyqtgraph's GLScatterPlotItem.

    positions: where each grid point ends up after passing through the LUT
    (the deformed lattice -- this is what makes the visualization useful).
    colors: the ORIGINAL/input RGB at that grid point, clipped to [0,1] for
    display -- this is the standard convention for LUT cube visualizations,
    so hue/saturation/luminance shifts are visible by comparing a point's
    position to its color.

    Uses the LUT's own .apply() (colour-science, verified correct), not any
    colour_clf_io execution path.
    """
    identity_grid = colour.LUT3D(size=subsample_size).table  # (n,n,n,3), domain [0,1]
    positions = np.asarray(lut.apply(identity_grid), dtype=float).reshape(-1, 3)
    colors = np.clip(identity_grid.reshape(-1, 3), 0.0, 1.0)
    return positions, colors


def extract_3d_cage_lines_for_visualization(lut, subsample_size=9):
    """Return an (2*E, 3) array of line-segment endpoints -- consecutive
    PAIRS of rows define one segment each, matching pyqtgraph's
    GLLinePlotItem(mode='lines') convention -- connecting each point in
    the LUT's deformed identity lattice to its immediate neighbors along
    each of the three grid axes. A wireframe "cage" of the SAME lattice
    extract_3d_points_for_visualization plots as a scatter (same
    subsample_size, same identity grid, same lut.apply() call), not a
    separate flat reference plane.

    Per Tom, correcting a misunderstanding in the original "grid opacity"
    feature: what he actually wanted was lines connecting the plotted
    points themselves (revealing how the LUT deforms the lattice as a
    mesh, the same way a wireframe cube visualizes a 3D transform), not a
    flat ground-plane grid unrelated to the LUT's own data -- see
    lut_viewer_3d.py's docstring for how this replaces the old
    `GLGridItem`.

    E = 3 * n^2 * (n-1) for an n=subsample_size grid (n-1 edges along
    each of 3 axes, times n^2 lines per axis) -- grows fast with density
    (811,200 edges at the slider's max, n=65), same order of magnitude as
    the n^3 scatter points this app already asks pyqtgraph/OpenGL to
    handle at that density, so no separate cap is added here -- flag if
    that turns out to be too slow in practice on real hardware.
    """
    identity_grid = colour.LUT3D(size=subsample_size).table  # (n,n,n,3)
    positions = np.asarray(lut.apply(identity_grid), dtype=float)  # (n,n,n,3) -- NOT flattened

    segments = []
    for axis in range(3):
        n = positions.shape[axis]
        if n < 2:
            continue
        starts = np.take(positions, range(0, n - 1), axis=axis).reshape(-1, 3)
        ends = np.take(positions, range(1, n), axis=axis).reshape(-1, 3)
        interleaved = np.empty((starts.shape[0] * 2, 3), dtype=float)
        interleaved[0::2] = starts
        interleaved[1::2] = ends
        segments.append(interleaved)

    if not segments:
        return np.zeros((0, 3), dtype=float)
    return np.concatenate(segments, axis=0)


def compute_yrgb_curve(lut, num_samples=256):
    """Return the LUT's response as four 1D curves (Y/R/G/B vs. input) --
    the "1D graph" view of a 3D LUT, per Tom's request, worked into the
    same panel as the 3D point-cloud view (lut_viewer_3d.py) rather than
    a separate Scopes mode, since it's a second projection of the exact
    same baked-LUT data the 3D view already shows, not something derived
    from a sample image.

    The ramp fed in follows the LUT's OWN declared domain (`lut.domain`,
    the same `[[min_r,min_g,min_b],[max_r,max_g,max_b]]` array
    stack.bake_stack_to_lut3d writes from Stack.domain_min/domain_max --
    see that function's own docstring) rather than a hardcoded [0,1], per
    Tom -- this app supports non-[0,1] domains (HDR grades, scene-linear
    stages) and the 3D Viewer's camera-centering fix already had to learn
    the same lesson (see lut_viewer_3d.py's CAMERA-CENTERING BUG note).
    One shared parameter `t` (0 to 1) sweeps every channel through ITS
    OWN [domain_min, domain_max] in lockstep -- when the domain is linked
    across channels (Stack's usual "Link R/G/B" default), this is exactly
    the familiar R=G=B neutral ramp; when it isn't, each channel still
    advances through its own real range together rather than assuming
    [0,1] regardless of what the Stack's domain settings actually are.
    Falls back to [0,1] if `lut` has no `.domain` attribute at all (e.g.
    an object that doesn't expose one).

    Returns a dict: 'input_r'/'input_g'/'input_b' (each (num_samples,),
    the actual per-channel values fed in -- the correct X axis for that
    channel's own curve, NOT necessarily identical to the other two
    channels' input arrays when the domain isn't linked) and 'r'/'g'/'b'/
    'y' (each (num_samples,), the LUT's output response). Y (luma) uses
    the same Rec.709 coefficients as the Histogram/Waveform scopes (see
    scopes.LUMA_COEFFICIENTS_REC709) -- a fixed, standard conversion, not
    a claim about "correct" luma for whatever space the LUT's output
    actually is in.
    """
    domain = getattr(lut, "domain", None)
    if domain is None:
        lo = np.zeros(3, dtype=float)
        hi = np.ones(3, dtype=float)
    else:
        domain_arr = np.asarray(domain, dtype=float)
        lo, hi = domain_arr[0], domain_arr[1]

    t = np.linspace(0.0, 1.0, num_samples)
    input_rgb = lo[np.newaxis, :] + t[:, np.newaxis] * (hi - lo)[np.newaxis, :]

    output_rgb = np.asarray(lut.apply(input_rgb), dtype=float).reshape(-1, 3)
    y = output_rgb @ np.asarray(LUMA_COEFFICIENTS_REC709, dtype=float)

    return {
        "input_r": input_rgb[:, 0],
        "input_g": input_rgb[:, 1],
        "input_b": input_rgb[:, 2],
        "r": output_rgb[:, 0],
        "g": output_rgb[:, 1],
        "b": output_rgb[:, 2],
        "y": y,
    }


def create_identity_lut(size=33, name="Identity"):
    """A pass-through colour.LUT3D (output == input) at the given grid
    size -- lets you start a session from a neutral LUT rather than
    needing a real file loaded first. Useful for previewing a DRT/bake
    (ACES Output Transform, Retarget, etc.) "on its own," with nothing
    else stacked underneath it, or as a blank first stage to build a
    Concatenate chain onto.

    colour.LUT3D(size=size) with no table argument already constructs
    exactly this -- its default table IS the identity grid (domain
    [0,1], table[i] == grid point i) -- so this function is mostly a
    documented, discoverable name for that fact rather than new math.
    Verified below (test_create_identity_lut_is_a_true_passthrough) that
    .apply() actually leaves values unchanged, not just that the table
    looks identity-shaped.
    """
    return colour.LUT3D(size=size, name=name)


def concatenate_luts(luts, size=None, name=None):
    """Combine multiple LUTs (each anything with .apply(RGB)) into one
    flat colour.LUT3D by composing them in order: an identity grid is
    passed through luts[0], the result through luts[1], and so on. Order
    matters -- this is function composition, not a commutative merge.

    Same "trust .apply(), don't hand-roll interpolation" rule as every
    other baking helper in this app (bake_process_list_to_lut3d,
    bake_retargeted_lut) -- each LUT's own verified apply() does the real
    work, this just chains the outputs.

    size defaults to the largest LUT3D among the inputs' own resolution
    (so combining doesn't quietly throw away resolution), else 33 if none
    of the inputs are LUT3Ds (e.g. concatenating LUT1D curves only).

    No clipping is applied to the result -- intermediate or final values
    outside [0,1] are legitimate (e.g. an HDR or scene-linear stage in the
    chain) and are preserved, same as bake_process_list_to_lut3d.
    """
    luts = list(luts)
    if not luts:
        raise ValueError("concatenate_luts needs at least one LUT")

    if size is None:
        sizes_3d = [
            lut.table.shape[0] for lut in luts
            if getattr(lut, "table", None) is not None and np.ndim(lut.table) == 4
        ]
        size = max(sizes_3d) if sizes_3d else 33

    identity_grid = colour.LUT3D(size=size).table
    result = identity_grid
    for lut in luts:
        result = np.asarray(lut.apply(result), dtype=float)

    if name is None:
        parts = [getattr(lut, "name", None) or "lut" for lut in luts]
        name = " -> ".join(parts)

    return colour.LUT3D(table=result, size=size, name=name)


def concatenate_luts_with_opacity(stages, size=None, name=None):
    """Same idea as concatenate_luts, but each stage is (lut, opacity)
    instead of a bare lut -- opacity in [0,1], the fraction of that
    stage's fully-applied output to blend in versus leaving that stage's
    input unchanged. This is the "LUT strength" control real grading
    tools offer per layer (Resolve/Baselight-style), not a Photoshop-style
    blend MODE -- just a linear crossfade between "before this stage" and
    "after this stage."

    At opacity=1.0 for every stage, this produces EXACTLY what
    concatenate_luts does for the same luts (verified in
    test_concatenate.py) -- it's a strict generalization, not a different
    algorithm, which is what makes it safe to point every chain-consuming
    caller at this instead of concatenate_luts without changing behavior
    for anyone who never touches the opacity control (it defaults to 1.0
    everywhere it's exposed in the GUI). opacity=0.0 for a stage is an
    exact no-op for that stage, same as if it weren't in the chain at all.

    `stages`: list of (lut, opacity) tuples. size/name defaulting rules
    match concatenate_luts (largest LUT3D input's resolution, or 33).
    """
    stages = list(stages)
    if not stages:
        raise ValueError("concatenate_luts_with_opacity needs at least one stage")

    luts = [lut for lut, _opacity in stages]
    if size is None:
        sizes_3d = [
            lut.table.shape[0] for lut in luts
            if getattr(lut, "table", None) is not None and np.ndim(lut.table) == 4
        ]
        size = max(sizes_3d) if sizes_3d else 33

    identity_grid = colour.LUT3D(size=size).table
    running = identity_grid
    for lut, opacity in stages:
        stage_output = np.asarray(lut.apply(running), dtype=float)
        if opacity >= 1.0:
            running = stage_output  # avoid needless float error at the common case
        elif opacity <= 0.0:
            pass  # exact no-op, running unchanged
        else:
            running = running * (1.0 - opacity) + stage_output * opacity

    if name is None:
        parts = [getattr(lut, "name", None) or "lut" for lut in luts]
        name = " -> ".join(parts)

    return colour.LUT3D(table=running, size=size, name=name)


def apply_lut_chain_with_opacity(stages, image_array):
    """Same relationship to concatenate_luts_with_opacity as
    apply_lut_chain_to_image_array has to concatenate_luts: applies each
    (lut, opacity) stage directly to the actual image pixels in sequence,
    no intermediate grid, for exact live-preview accuracy while a chain
    (with per-stage opacity) is being built. `stages`: list of
    (lut, opacity) tuples; empty list returns the image unchanged."""
    result = np.asarray(image_array, dtype=np.float32)
    for lut, opacity in stages:
        stage_output = apply_lut_to_image_array(lut, result)
        if opacity >= 1.0:
            result = stage_output
        elif opacity <= 0.0:
            pass
        else:
            result = (result * (1.0 - opacity) + stage_output * opacity).astype(np.float32)
    return result


DEFAULT_SAMPLE_IMAGE_FILENAME = "default_sample_image_acescct_ap1.tif"
DEFAULT_SAMPLE_IMAGE_SOURCE_COLOURSPACE = "ACEScct"


def default_sample_image_path():
    """Path to the bundled default reference still (ACEScct, AP1 primaries,
    baked into an 8-bit TIFF as code values -- same "raw numeric domain"
    convention load_image_as_array uses for everything else). Lives in
    assets/ alongside this file so it resolves the same way whether run
    from source (python3 main_window.py in this folder) or from a packaged
    .app (py2app's DATA_FILES puts assets/ under Contents/Resources
    alongside the rest of this package -- see setup.py). Returns a Path
    that may not exist (e.g. a checkout missing the asset); callers should
    check .exists() rather than assume it's there, same as any other
    optional startup resource.

    NOTE: this can't just be Path(__file__).parent -- py2app zips this
    module's .pyc into Contents/Resources/lib/python3XX.zip (it's an
    `includes` entry, not the APP entry script), so __file__ at runtime
    is something like ".../python311.zip/app_logic.pyc" and its
    "parent" is the zip archive itself, not Contents/Resources. assets/
    is a sibling of the zip (py2app's DATA_FILES puts it straight under
    Contents/Resources), so a frozen build has to anchor on
    RESOURCEPATH (which py2app's __boot__ sets in the environment)
    instead of __file__."""
    import os
    import sys

    if getattr(sys, "frozen", False) and "RESOURCEPATH" in os.environ:
        base = Path(os.environ["RESOURCEPATH"])
    else:
        base = Path(__file__).parent
    return base / "assets" / DEFAULT_SAMPLE_IMAGE_FILENAME


def get_image_native_size(path):
    """(width, height) of the source file without decoding its raster data
    -- delegates to image_io.get_native_size (header-only read, no pixel
    decode -- see that module's own docstring). Used by
    sample_image_panel's resize handling to know when the preview is
    already at full native resolution (no point requesting a bigger
    max_dimension than the source actually has)."""
    return image_io.get_native_size(path)


def load_image_as_array(path, max_dimension=480):
    """Load an image file into a numpy float32 (H,W,3) array, RGB channel
    order -- delegates the actual decode to image_io.read_image_as_array
    (Tom, 2026-08-13: real 10/12/16-bit and float/EXR support, not just
    8-bit -- see that module's own docstring for the bit-depth/scale
    conventions). Integer sources land in [0,1]; float (EXR/HDR) sources
    pass through unscaled, unbounded.

    This is a PREVIEW helper, not an export-quality pipeline: downsamples
    (aspect-preserving, Lanczos, via image_io.resize_array -- operates
    directly on the float array, so no precision is lost through an
    intermediate 8-bit round trip) so the longer side is at most
    max_dimension, and does NO color management. That's the same "raw
    numeric domain" convention the rest of this app uses (see
    extract_3d_points_for_visualization above): if your sample image isn't
    already in whatever working space the LUT expects, the before/after
    preview won't look photographically "correct" -- it'll show exactly
    what the LUT does to those numbers, which is the honest thing to show
    for a tool whose job is inspecting LUTs, not color-managing images.
    max_dimension=None disables downsampling.
    """
    array, bit_depth = image_io.read_image_as_array(path)
    if max_dimension is not None:
        h, w = array.shape[:2]
        scale = max_dimension / max(w, h)
        if scale < 1.0:
            new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
            array = image_io.resize_array(array, new_w, new_h)
            if bit_depth != "32f":
                # Lanczos has negative lobes -- a resize can legitimately
                # ring a few 1e-4 outside [0,1] near a hard edge, even for
                # already-in-range source data (confirmed directly: PIL's
                # old 8-bit-container resize silently clipped this via
                # its uint8 round trip, so it was never visible before
                # image_io.resize_array started operating on the full
                # float domain). An integer-sourced image can never
                # LEGITIMATELY have data outside [0,1] to begin with, so
                # clipping here only removes filter artifacts, never real
                # signal -- unlike a true "32f" source, where values
                # outside [0,1] are the actual HDR content and must
                # survive (see image_io.write_image's own "don't silently
                # discard legitimate HDR range" rule).
                array = np.clip(array, 0.0, 1.0)
    return array


def apply_lut_to_image_array(lut, image_array):
    """Apply lut.apply() to an (H,W,3) image array, clipped to [0,1] for
    display. Uses the LUT's own .apply() -- the same verified path used
    everywhere else in this app (extract_3d_points_for_visualization, the
    export round-trip tests), not a separate reimplementation, so this
    inherits the same correctness guarantees rather than being a new
    thing to distrust."""
    result = np.asarray(lut.apply(image_array), dtype=np.float64)
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def apply_lut_chain_to_image_array(luts, image_array):
    """Apply a sequence of LUTs to an (H,W,3) image array in order, each
    stage feeding the next -- for live-previewing an in-progress
    Concatenate chain (see concatenate_panel.py) against the sample image
    WITHOUT baking it into a fixed-resolution LUT3D first.

    This is deliberately a separate code path from concatenate_luts:
    concatenate_luts samples an identity grid through the chain and bakes
    a single LUT3D (introducing grid-interpolation error, but producing a
    real exportable/reusable LUT). This function applies each stage
    directly to the actual image pixels with no intermediate grid, so the
    live preview is exact, not an approximation of what Concatenate would
    eventually bake. An empty `luts` list returns the image unchanged.
    """
    result = np.asarray(image_array, dtype=np.float32)
    for lut in luts:
        result = apply_lut_to_image_array(lut, result)
    return result


def list_available_colourspaces():
    """Sorted list of color space names usable with retarget_io/convert --
    the full colour-science registry plus anything in
    colorspace.CUSTOM_COLOURSPACES. Kept here (not hardcoded in the GUI
    layer) so a custom colourspace registered later shows up in the combo
    box automatically."""
    from colorspace import CUSTOM_COLOURSPACES
    return sorted(set(colour.RGB_COLOURSPACES.keys()) | set(CUSTOM_COLOURSPACES.keys()))


def bake_retargeted_lut(lut, from_space, to_space, size=None,
                         chromatic_adaptation_transform="CAT02", name=None):
    """Wrap `lut` with retarget_io(from_space, to_space) and bake the result
    into a new flat colour.LUT3D, ready to view/export/further-edit like any
    other LUT in this app.

    retarget_io returns a plain callable (wrapped(RGB)), not an object with
    .apply() -- see retarget.py's own docstring -- so it can't be dropped
    into the rest of this app (viewer, exporters, image preview) as-is.
    Baking it onto an identity grid, the same way bake_process_list_to_lut3d
    already does for CLF node chains, is what makes retargeting a first-class
    "LUT" this app can treat like any other from that point on.

    size defaults to the source LUT's own resolution if it's a LUT3D
    (so baking doesn't quietly throw away resolution), else 33.
    """
    if size is None:
        table = getattr(lut, "table", None)
        size = table.shape[0] if table is not None and np.ndim(table) == 4 else 33

    wrapped = retarget_io(
        lut, from_space=from_space, to_space=to_space,
        chromatic_adaptation_transform=chromatic_adaptation_transform)

    identity_grid = colour.LUT3D(size=size).table
    baked_table = np.asarray(wrapped(identity_grid), dtype=float)
    return colour.LUT3D(
        table=baked_table, size=size,
        name=name or f"{getattr(lut, 'name', None) or 'lut'}_{from_space}_to_{to_space}",
    )


def resample_lut(lut, new_size):
    """Resample a colour.LUT1D or LUT3D to a new resolution, preserving its
    current domain (see set_lut_domain below -- resize and domain-editing
    compose correctly because of this).

    Implementation: sample an identity ramp/grid rescaled into the LUT's
    own domain, and evaluate it through the LUT's own .apply() -- same
    "trust .apply(), don't hand-roll interpolation" rule as everywhere else
    in this app, so this inherits colour-science's verified interpolation
    rather than being a new thing to distrust.
    """
    domain = np.asarray(lut.domain, dtype=float)
    table = getattr(lut, "table", None)
    is_3d = table is not None and np.ndim(table) == 4

    if is_3d:
        identity = colour.LUT3D(size=new_size).table  # [0,1] domain
        lo, hi = domain[0], domain[1]
        scaled_grid = identity * (hi - lo) + lo
        new_table = np.asarray(lut.apply(scaled_grid), dtype=float)
        new_lut = colour.LUT3D(table=new_table, size=new_size, name=lut.name)
    else:
        lo = float(np.ravel(domain)[0])
        hi = float(np.ravel(domain)[-1])
        ramp = np.linspace(lo, hi, new_size)
        new_table = np.asarray(lut.apply(ramp), dtype=float)
        new_lut = colour.LUT1D(table=new_table, name=lut.name)

    new_lut.domain = domain
    return new_lut


def set_lut_domain(lut, domain_min, domain_max):
    """Return a copy of `lut` with its .domain replaced -- does NOT
    resample the stored table, only changes what input range that table
    represents.

    Per colour-science's own LUT.apply() semantics (verified in
    test_lut_controls.py): a value equal to domain_min maps to the LUT's
    first table entry, domain_max to its last, values in between are
    interpolated proportionally, values outside are extrapolated/clipped
    by whatever .apply() itself does. This is genuinely an "edit the input
    bounds" operation, not a resample -- combine with resample_lut if you
    also want the table itself resampled onto a new domain.

    domain_min/domain_max: length-3 sequences for a LUT3D, scalars (or
    length-1) for a LUT1D.
    """
    new_lut = lut.copy()
    table = getattr(lut, "table", None)
    is_3d = table is not None and np.ndim(table) == 4

    if is_3d:
        new_lut.domain = np.array(
            [np.broadcast_to(domain_min, (3,)), np.broadcast_to(domain_max, (3,))],
            dtype=float,
        )
    else:
        new_lut.domain = np.array(
            [np.ravel(domain_min)[0], np.ravel(domain_max)[0]], dtype=float
        )
    return new_lut


def _float_array_to_pil_image(image_array):
    """(H,W,3) float array in [0,1] -> PIL.Image, uint8 RGB. Shared by
    array_to_png_bytes (in-memory, for Qt preview pixmaps) and
    export_image_array_to_file (to-disk, for Export Still)."""
    uint8_array = np.clip(image_array * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(uint8_array, mode="RGB")


def array_to_png_bytes(image_array):
    """(H,W,3) float array in [0,1] -> PNG-encoded bytes. Kept Qt-free
    (returns plain bytes, not a QPixmap/QImage) so this stays testable
    here and the GUI layer only has to do QPixmap.loadFromData(...) on
    the result."""
    img = _float_array_to_pil_image(image_array)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_bytes_to_array(png_bytes):
    """Inverse of array_to_png_bytes -- PNG-encoded bytes -> (H,W,3)
    float32 array in [0,1]. Used to reload a StackMemory's captured
    full_still (Stills Gallery, Phase 3 follow-up) for "Load as
    Reference", since that bytes-only capture has no source_path to
    reload from the way a GalleryStill or ImageLibraryEntry does."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    return np.asarray(img, dtype=np.float32) / 255.0


def export_image_array_to_file(image_array, path, bit_depth=8):
    """Save an (H,W,3) float array to a still image file on disk --
    format inferred from path's extension (.png/.tif/.exr/.dpx/etc,
    whatever image_io/OIIO's plugin set supports), quantized to
    `bit_depth` (one of canvas.BIT_DEPTHS -- see image_io.write_image's
    own docstring). Defaults to 8-bit, matching every existing caller's
    prior behavior exactly -- callers that want Canvas Settings' own bit
    depth pass it explicitly. Used by Export Still; kept separate from
    array_to_png_bytes since that one is PNG-only, always-8-bit, and
    in-memory by design (for preview pixmaps), not meant to double as a
    general file writer."""
    image_io.write_image(image_array, str(path), bit_depth)


def render_full_resolution_still(source_path, lut=None, lut_chain=None, lut_chain_stages=None):
    """Reload the sample image at FULL resolution (no downsampling) from
    source_path and apply a single LUT, a plain chain, or an
    opacity-aware chain, matching whichever SampleImagePanel is currently
    previewing. Exactly one of lut/lut_chain/lut_chain_stages must be
    given.

    lut_chain (a plain list of luts, via apply_lut_chain_to_image_array)
    is kept alongside the newer lut_chain_stages (a list of (lut,
    opacity) tuples, via apply_lut_chain_with_opacity) rather than
    replaced, so existing callers/tests that don't care about opacity
    keep working unchanged.

    Exists specifically so Export Still produces a real, full-quality
    result instead of reusing whatever the panel's preview happens to be
    showing at that instant -- Sample/Reference load at native resolution
    (see sample_image_panel.py's "PREVIEW RESOLUTION" comment), but the
    preview can transiently be a lower-resolution render mid-edit-burst
    (see that module's EDIT_DOWNRES_FACTOR), which is exactly the kind of
    throwaway, intentionally-approximate frame that should never be what
    actually gets handed to a peer as a deliverable.
    """
    given = [x is not None for x in (lut, lut_chain, lut_chain_stages)]
    if sum(given) != 1:
        raise ValueError(
            "render_full_resolution_still needs exactly one of "
            "lut, lut_chain, or lut_chain_stages")
    full_res = load_image_as_array(source_path, max_dimension=None)
    if lut is not None:
        return apply_lut_to_image_array(lut, full_res)
    if lut_chain is not None:
        return apply_lut_chain_to_image_array(lut_chain, full_res)
    return apply_lut_chain_with_opacity(lut_chain_stages, full_res)
