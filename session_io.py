"""
session_io.py -- save/load a LUTier session (the Stack + Sample/Reference
image state + the Stack Memories/CDL/LUT/Image libraries) as a single .ltr
JSON file. Qt-free, like every other backend module here (stack.py,
app_logic.py, ...), so the actual serialize/deserialize logic is testable
without a display -- the GUI layer (main_window.py) just calls
save_session/load_session and hands the result to StackPanel.set_stack /
SampleImagePanel.load_session_state / StackMemoriesPanel.load_library /
CDLLibraryPanel.load_library / LUTLibraryPanel.load_library /
ImageLibraryPanel.load_library.

WHAT'S SAVED: the working Stack (every stage's kind/label/enabled/opacity/
params, Stack.size/domain_min/domain_max), the Sample and Reference image
references (path + adaptation state each -- see
sample_image_panel.get_session_state), every saved Stack Memory (see
stack_memory.py) -- each memory is its own independent stack, serialized
the exact same way the working stack is (memory_library_to_session_list
just calls stack_to_session_dict per memory) -- every saved CDL Library
entry (see cdl_library.py), every saved LUT Library entry (see
lut_library.py), and every saved Image Library entry (see
image_library.py -- note this one is path-referenced, not embedded, see
VERSION 5 below). WORKSPACE LAYOUT (window size, which panels are shown/
hidden or floating, splitter sizes) is deliberately NOT part of this list
as of VERSION 7 -- see that version's own note further down, and
workspace_io.py's docstring for where it actually lives now. The Viewer's
wipe enable/side-selection state remains deliberately unsaved too -- a
transient viewing choice, not project state, same reasoning that now also
covers workspace layout.

STACK FORKING (per Tom): "fork_split"/"fork_merge" are ordinary Stage
kinds like any other -- fork_split just marks where a region of the
flat stage list opens into two parallel branches, fork_merge marks
where it closes back into one signal (see stack.py's docstring, "STACK
FORKING" for the full evaluation semantics -- this module has nothing
structural to do with that, it only reads/writes fields). Every stage
entry (not just fork ones) now carries a "branch" field -- None for an
ordinary stage, "a"/"b" for a stage meant to sit inside a fork region --
written by stage_to_session_dict and restored by stage_from_session_dict
via plain post-construction attribute assignment (stage.branch = ...),
the same way stack.py's own tests tag a branch onto an otherwise-normal
Stage; none of the make_xxx_stage helpers take a branch kwarg, branch
isn't part of any kind's construction, just an orthogonal tag any stage
can carry. fork_split has no extra fields (no meaningful opacity either,
per stack.py's docstring); fork_merge additionally carries "blend" (its
inverse a/b crossfade -- opacity is already covered by the base entry's
"opacity" field like every other stage, and per Tom serves as fork_merge's
"total opacity control like every other operator row"). Because branch
stages are just ordinary flat-list entries now (not nested inside a fork
stage's params the way an earlier single-stage design briefly was), no
recursion is needed here at all -- stack_to_session_dict/
stack_from_session_dict's existing per-stage loop already round-trips a
whole fork region correctly with zero special-casing beyond the two
kind branches in stage_to_session_dict/stage_from_session_dict below. No
SESSION_VERSION bump needed for this -- "stages" already existed since
version 1, fork_split/fork_merge are just new values the "kind" field
can take and "branch" is just a new field on every stage entry
(defaulting to None via .get("branch") on load), and an older app
version opening a session with one already degrades exactly the way any
unrecognized kind always has (skipped with a "from a newer app version?"
warning, see stage_from_session_dict) -- forward compatibility this
format already had, not something added for forks specifically.

LUT STAGE PROVENANCE: a "lut" stage's params always includes a real
colour.LUT3D/LUT1D object, which isn't JSON-serializable and shouldn't be
embedded as a giant baked table anyway (that's what .cube/.clf export is
for) -- what actually needs to survive a save/load round trip is HOW to
rebuild the same LUT, not the LUT itself. stack_panel.py tags every "lut"
stage's params with that provenance at creation time (source_kind: "file"
+ source_path, or source_kind: "identity" + identity_size) -- see
add_lut_stage_from_path/add_identity_stage there. This module just reads
those tags back out on save (stack_to_session_dict) and reconstructs via
the exact same loaders (load_lut_any_format/create_identity_lut) on load
(stack_from_session_dict), re-tagging the rebuilt stage the same way so a
LOADED session (or a loaded memory) can be re-saved without losing
provenance. This applies equally to memories, since they're serialized
through the same stack_to_session_dict/stack_from_session_dict pair.

A missing/moved source file, or any other single-stage reconstruction
failure, is reported as a WARNING for that one stage (skipped, not a hard
failure for the whole session) -- same "don't let one bad reference wreck
everything else" philosophy as this app's other loaders (see
BrowserPanel/app_logic's own error handling). The same applies per-memory:
one broken memory doesn't block the rest of the session or the other
memories from loading.

VERSION 2 added stack_memories (version 1 files -- from before Stack
Memories existed -- still load fine, just with an empty memory library,
via the .get("stack_memories", []) default below).

VERSION 3 added cdl_library (see cdl_library.py) -- version 1/2 files
still load fine, just with an empty CDL Library, via the
.get("cdl_library", []) default below. Unlike stack_memories/the working
stack, a CDL Library entry has NOTHING to reconstruct-by-reference (no
file path, no LUT object) -- every value is a plain float/tuple already,
so cdl_library_from_session_list can't fail per-entry the way
stack_from_session_dict can, and returns no warnings.

VERSION 4 added lut_library (see lut_library.py) -- version 1/2/3 files
still load fine, just with an empty LUT Library, via the
.get("lut_library", []) default below. A LUT Library entry's whole POINT
is to embed its full table directly (lut_to_embedded_dict/
embedded_dict_to_lut below) rather than reference a file path -- see
lut_library.py's docstring on why -- so lut_library_from_session_list can
fail per-entry (a malformed/future-format table) the same way
stack_from_session_dict can, and reports warnings the same way. This same
embedded-table representation is reused for a "lut" stage tagged
source_kind="embedded" (added via
stack_panel.add_lut_stage_from_library_entry) -- see stack_to_session_dict/
stack_from_session_dict's "embedded" branch below -- so a Stack stage
built from the LUT Library survives a session move independent of
whether the library entry it came from still exists.

VERSION 5 added image_library (see image_library.py) and the sample_image
dict's optional "reference" sub-dict (see
sample_image_panel.get_session_state/load_session_state) -- version 1-4
files still load fine, with an empty Image Library and no Reference
image, via the .get("image_library", [])/sample_image_state.get(
"reference") defaults. UNLIKE the LUT Library, an Image Library entry is
NOT embedded by value here -- image_library_to_session_list writes only
name/source_path/source_colourspace and image_library_from_session_list
RE-DECODES the file fresh on load, same "how to rebuild it, not the
built thing" principle as the sample image's own path-based persistence
-- see image_library.py's docstring for why (a decoded image is orders
of magnitude bigger than a LUT table, so embedding it directly the way
lut_to_embedded_dict does would bloat the .ltr file for no real benefit).
Practical consequence: an Image Library entry (and Reference, if it came
from one) can fail to reconstruct on load if its source file has since
moved -- reported as a warning, same "one bad thing doesn't sink
everything else" rule as every other per-entry failure in this module,
but a real difference from LUT/CDL Library's stronger guarantee, worth
knowing before relying on it across a machine migration.

VERSION 6 added "workspace" (see above) -- version 1-5 files still load
fine with no workspace section at all, via the .get("workspace", {})
default below, which main_window.load_workspace_state treats the same as
an explicit empty dict (no-op, leaving whatever _build_layout's own
defaults already set).

VERSION 7 REMOVES "workspace" again (per Tom) -- reverses VERSION 6's own
reversal, one checkpoint later: workspace layout turned out to be an
app-level UI preference ("how do I like my panels arranged"), not project
data that should travel with a specific .ltr file -- see workspace_io.py's
docstring for where it actually lives now (an auto-remembered file plus
named, explicitly exported/imported presets, both independent of which
session happens to be open). save_session no longer accepts a `workspace`
argument at all and never writes the key; load_session no longer returns
it either -- callers that want the OLD signature's workspace element are
the one thing that breaks across this version bump (see main_window.py's
_write_session/_open_session, updated to match). Reading a VERSION 6 file
that still has a "workspace" key is harmless -- load_session simply never
looks at it, same as it's always ignored any key this version's format
doesn't define."""
import json

import numpy as np
import colour

from app_logic import load_lut_any_format, create_identity_lut, load_image_as_array
import stack as st
import canvas as cv
import video_source
import cdl
import custom_drt
import exposure_contrast
import grading_primary
import grading_rgb_curve
import grading_tone
from stack_memory import StackMemory, StackMemoryLibrary, GalleryStill, GalleryStillLibrary
from cdl_library import CDLEntry, CDLLibrary
from lut_library import LUTLibraryEntry, LUTLibrary
from image_library import ImageLibraryEntry, ImageLibrary
from atomic_write import atomic_open

SESSION_FORMAT = "lutier-session"
SESSION_VERSION = 10

# VERSION 10 (Media Library Phase 2 + Stills Gallery Phase 3, per Tom's
# "comprehensive media library" / "stack memories ... enhanced to be a
# stills gallery" requests) extends each image_library entry with
# media_kind/frame_count/frame_rate/duration/thumbnail -- see
# image_library_to_session_list/image_library_from_session_list's own
# docstrings -- and each memory_library entry with thumbnail (see
# memory_library_to_session_list/memory_library_from_session_list), and
# adds a brand-new top-level "gallery_stills" list (see
# gallery_stills_to_session_list/gallery_stills_from_session_list) for
# GalleryStill entries, deliberately its own key rather than folded into
# image_library (Tom's own "we're not folding it into the media
# library"). A file saved before this version has none of these keys;
# each is defaulted per-field on load (media_kind -> "still", the rest
# -> None; gallery_stills -> an empty list), same "old file loads fine,
# just without the newer field" convention every prior version bump in
# this module already follows.

# VERSION 9 adds "canvas" (see canvas.py -- Tom, 2026-08-13: "Canvas
# settings (Resolution / Bit Depth / Pixel Aspect)"): the project-level
# CanvasSettings MainWindow now owns alongside the Stack/undo history. A
# file with no "canvas" key (version < 9, or a version 9+ file saved
# with canvas=None) loads with canvas.DEFAULT_CANVAS, same "old file
# loads fine, just without the newer feature" convention every prior
# version bump in this module already follows.

# VERSION 8 adds "undo_history" (per Tom, "reset on new/open session but
# keep undo history inside of project data if possible"): MainWindow now
# resets undo history to a single fresh state on New/Open Session instead
# of letting it span unrelated documents (see undo_history.py's SCOPE ACROSS
# SESSION BOUNDARIES note), but a session that WAS saved with history still
# has it restored when that same session is reopened. Capped at
# MAX_PERSISTED_UNDO_STATES: unlike the in-memory case (cheap by
# construction, see undo_history.py), each persisted state is a full
# stack_to_session_dict serialization, which duplicates any embedded LUT
# table independently per state rather than sharing a reference to it --
# an uncapped history here would make a long editing session's .ltr file
# grow without bound. A file missing this key (version < 8, or version 8
# saved with no undo_history passed in) simply loads with no persisted
# history, same "old file loads fine, just without the newer feature"
# convention every prior version bump in this module already follows.
MAX_PERSISTED_UNDO_STATES = 50


def lut_to_embedded_dict(lut):
    """A LUT (colour.LUT1D/LUT3D) -> a plain JSON-able dict embedding its
    FULL table directly (name/table/domain/comments), no file path -- used
    both for LUT Library entries (lut_library_to_session_list) and for a
    "lut" stage sourced from the LUT Library (source_kind: "embedded", see
    stack_to_session_dict). `ndim` records the table's original
    dimensionality (4 for a LUT3D's (n,n,n,3) table, 1 or 2 for a LUT1D's
    (n,) or (n,3) table) so embedded_dict_to_lut can reconstruct the right
    LUT class on the way back in without guessing."""
    table = np.asarray(lut.table, dtype=float)
    data = {
        "name": getattr(lut, "name", None),
        "table": table.tolist(),
        "ndim": int(table.ndim),
    }
    domain = getattr(lut, "domain", None)
    if domain is not None:
        data["domain"] = np.asarray(domain, dtype=float).tolist()
    comments = getattr(lut, "comments", None)
    if comments:
        data["comments"] = list(comments)
    return data


def embedded_dict_to_lut(data):
    """Inverse of lut_to_embedded_dict. A table with ndim==4 is a LUT3D's
    (n,n,n,3) grid; anything else (1D or (n,3)) is a LUT1D -- same
    distinction colour-science itself draws between the two classes."""
    table = np.asarray(data["table"], dtype=float)
    name = data.get("name")
    comments = data.get("comments") or None
    lut_cls = colour.LUT3D if table.ndim == 4 else colour.LUT1D
    lut = lut_cls(table=table, name=name, comments=comments)
    if data.get("domain") is not None:
        lut.domain = np.asarray(data["domain"], dtype=float)
    return lut


def stage_to_session_dict(stage):
    """One stack.Stage -> plain JSON-able dict (kind/label/enabled/opacity/
    branch plus whatever fields that kind needs). Raises ValueError if the
    stage has no reconstructable provenance (a "lut" stage with an
    unrecognized/missing source_kind) -- shouldn't happen for anything
    created through the normal add_lut_stage_from_path/add_identity_stage
    paths, but if some other path ever produces one, failing loudly here
    is deliberate: this used to silently return None and have
    stack_to_session_dict drop the stage with zero indication anything
    was lost, asymmetric with the load path (stage_from_session_dict),
    which always surfaces a reconstruction failure as a warning rather
    than silently discarding it. A save that can't represent a stage
    should fail the whole save (main_window's existing "Failed to save
    session" error dialog around session_io.save_session already handles
    surfacing this), not silently produce a session file quietly missing
    a stage the user never gets told about. Every entry carries "branch"
    (None/"a"/"b", see this module's docstring on STACK FORKING)
    regardless of kind, since branch is an orthogonal tag any stage can
    carry, not something specific to fork_split/fork_merge themselves."""
    entry = {
        "kind": stage.kind,
        "label": stage.label,
        "enabled": stage.enabled,
        "opacity": stage.opacity,
        "branch": stage.branch,
    }
    if stage.kind == "lut":
        source_kind = stage.params.get("source_kind")
        if source_kind == "file":
            entry["source"] = {"type": "file", "path": stage.params.get("source_path")}
        elif source_kind == "identity":
            entry["source"] = {"type": "identity", "size": stage.params.get("identity_size")}
        elif source_kind == "embedded":
            # From the LUT Library (see lut_library.py/stack_panel.py's
            # add_lut_stage_from_library_entry) -- the full table is
            # written directly into the session, no path to reload,
            # exactly the point of ingesting it into the library in
            # the first place.
            entry["source"] = {
                "type": "embedded",
                "lut": lut_to_embedded_dict(stage.params["lut"]),
            }
        else:
            raise ValueError(
                f'Cannot save "lut" stage {stage.label!r}: unrecognized or '
                f"missing source_kind {source_kind!r}")
        entry["retarget_from"] = stage.params.get("retarget_from")
        entry["retarget_to"] = stage.params.get("retarget_to")
    elif stage.kind == "printer_lights":
        entry["r_points"] = stage.params.get("r_points", 0.0)
        entry["g_points"] = stage.params.get("g_points", 0.0)
        entry["b_points"] = stage.params.get("b_points", 0.0)
        entry["working_colourspace"] = stage.params.get("working_colourspace")
    elif stage.kind == "aces_drt":
        entry["working_colourspace"] = stage.params.get("working_colourspace")
        entry["display"] = stage.params.get("display")
        entry["view"] = stage.params.get("view")
        entry["config_name"] = stage.params.get("config_name")
    elif stage.kind == "custom_drt":
        entry["working_colourspace"] = stage.params.get("working_colourspace")
        entry["contrast"] = stage.params.get("contrast", custom_drt.DEFAULT_CONTRAST)
        entry["headroom"] = stage.params.get("headroom", custom_drt.DEFAULT_HEADROOM)
        entry["blend_percentile"] = stage.params.get(
            "blend_percentile", custom_drt.DEFAULT_BLEND_PERCENTILE)
        entry["tail_width"] = stage.params.get("tail_width", custom_drt.DEFAULT_TAIL_WIDTH)
        entry["desat_start"] = stage.params.get("desat_start", custom_drt.DEFAULT_DESAT_START)
        entry["desat_end"] = stage.params.get("desat_end", custom_drt.DEFAULT_DESAT_END)
        entry["identity_zone"] = stage.params.get("identity_zone", custom_drt.DEFAULT_IDENTITY_ZONE)
        entry["transition_softness"] = stage.params.get(
            "transition_softness", custom_drt.DEFAULT_TRANSITION_SOFTNESS)
        entry["fullness_r"] = stage.params.get("fullness_r", custom_drt.DEFAULT_FULLNESS_R)
        entry["fullness_g"] = stage.params.get("fullness_g", custom_drt.DEFAULT_FULLNESS_G)
        entry["fullness_b"] = stage.params.get("fullness_b", custom_drt.DEFAULT_FULLNESS_B)
        entry["mastering_display"] = stage.params.get(
            "mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY)
        entry["final_target"] = stage.params.get("final_target")
        entry["mastering_brightness"] = stage.params.get(
            "mastering_brightness", custom_drt.DEFAULT_MASTERING_BRIGHTNESS)
        entry["config_name"] = stage.params.get("config_name")
    elif stage.kind == "cdl":
        entry["slope"] = list(stage.params.get("slope", cdl.IDENTITY_SLOPE))
        entry["offset"] = list(stage.params.get("offset", cdl.IDENTITY_OFFSET))
        entry["power"] = list(stage.params.get("power", cdl.IDENTITY_POWER))
        entry["sat"] = stage.params.get("sat", cdl.IDENTITY_SAT)
        entry["style"] = stage.params.get("style", cdl.DEFAULT_STYLE)
        entry["sop_enabled"] = stage.params.get("sop_enabled", True)
        entry["sat_enabled"] = stage.params.get("sat_enabled", True)
    elif stage.kind == "colorspace":
        entry["src"] = stage.params.get("src")
        entry["dst"] = stage.params.get("dst")
        entry["config_name"] = stage.params.get("config_name")
    elif stage.kind == "grading_primary":
        entry["style"] = stage.params.get("style", grading_primary.DEFAULT_STYLE)
        entry["brightness"] = list(stage.params.get("brightness", grading_primary.IDENTITY_ADD))
        entry["contrast"] = list(stage.params.get("contrast", grading_primary.IDENTITY_MULT))
        entry["gamma"] = list(stage.params.get("gamma", grading_primary.IDENTITY_MULT))
        entry["offset"] = list(stage.params.get("offset", grading_primary.IDENTITY_ADD))
        entry["exposure"] = list(stage.params.get("exposure", grading_primary.IDENTITY_ADD))
        entry["lift"] = list(stage.params.get("lift", grading_primary.IDENTITY_ADD))
        entry["gain"] = list(stage.params.get("gain", grading_primary.IDENTITY_MULT))
        entry["pivot"] = stage.params.get("pivot", grading_primary.DEFAULT_PIVOT)
        entry["saturation"] = stage.params.get("saturation", grading_primary.DEFAULT_SATURATION)
    elif stage.kind == "exposure_contrast":
        entry["style"] = stage.params.get("style", exposure_contrast.DEFAULT_STYLE)
        entry["exposure"] = stage.params.get("exposure", exposure_contrast.IDENTITY_EXPOSURE)
        entry["contrast"] = stage.params.get("contrast", exposure_contrast.IDENTITY_CONTRAST)
        entry["gamma"] = stage.params.get("gamma", exposure_contrast.IDENTITY_GAMMA)
    elif stage.kind == "grading_tone":
        entry["style"] = stage.params.get("style", grading_tone.DEFAULT_STYLE)
        for range_key in grading_tone.RANGE_KEYS:
            entry[range_key] = list(
                stage.params.get(range_key) or grading_tone.DEFAULT_RANGE_VALUES[range_key])
        entry["scontrast"] = stage.params.get("scontrast", grading_tone.DEFAULT_SCONTRAST)
    elif stage.kind == "grading_rgb_curve":
        entry["style"] = stage.params.get("style", grading_rgb_curve.DEFAULT_STYLE)
        for channel in grading_rgb_curve.RGB_CHANNELS:
            points = stage.params.get(channel) or grading_rgb_curve.DEFAULT_CONTROL_POINTS
            entry[channel] = [list(point) for point in points]
    elif stage.kind == "fork_split":
        pass  # no extra fields -- see module docstring, STACK FORKING
    elif stage.kind == "fork_merge":
        entry["blend"] = stage.params.get("blend", 0.5)
    else:  # pragma: no cover -- Stage() already validates kind
        raise ValueError(f"Cannot save stage {stage.label!r}: unknown kind {stage.kind!r}")
    return entry


def stack_to_session_dict(stack):
    """stack.Stack -> plain JSON-able dict, via stage_to_session_dict per
    stage (see that function for the per-kind field list and why an
    unreconstructable stage raises rather than being silently dropped)."""
    return {
        "size": stack.size,
        "domain_min": list(stack.domain_min),
        "domain_max": list(stack.domain_max),
        "stages": [stage_to_session_dict(stage) for stage in stack.stages],
    }


def stage_from_session_dict(entry):
    """Inverse of stage_to_session_dict -- one dict -> one Stage. Raises
    (ValueError, or whatever load_lut_any_format/embedded_dict_to_lut
    itself raises) on any reconstruction failure -- missing LUT file,
    unknown/future stage kind, malformed entry -- rather than
    warning-and-skipping itself. That's deliberately the CALLER's job:
    stack_from_session_dict below turns a raise here into one per-stage
    warning + skip.

    Every stage's "branch" field (None/"a"/"b") is restored the same way
    regardless of kind, via plain post-construction attribute assignment
    (stage.branch = entry.get("branch")) right before returning -- none
    of the make_xxx_stage helpers take a branch kwarg, so this can't be
    folded into any one kind's construction call above; see this module's
    docstring, STACK FORKING, and stack.py's Stage docstring."""
    kind = entry.get("kind")
    label = entry.get("label")
    if kind == "lut":
        source = entry.get("source") or {}
        if source.get("type") == "file":
            path = source.get("path")
            lut = load_lut_any_format(path)
            stage = st.make_lut_stage(
                lut, label=label,
                retarget_from=entry.get("retarget_from"),
                retarget_to=entry.get("retarget_to"),
                enabled=entry.get("enabled", True),
                opacity=entry.get("opacity", 1.0),
            )
            stage.params["source_kind"] = "file"
            stage.params["source_path"] = path
        elif source.get("type") == "identity":
            size = source.get("size", 33)
            lut = create_identity_lut(size=size, name=f"Identity_{size}")
            stage = st.make_lut_stage(
                lut, label=label,
                retarget_from=entry.get("retarget_from"),
                retarget_to=entry.get("retarget_to"),
                enabled=entry.get("enabled", True),
                opacity=entry.get("opacity", 1.0),
            )
            stage.params["source_kind"] = "identity"
            stage.params["identity_size"] = size
        elif source.get("type") == "embedded":
            lut = embedded_dict_to_lut(source["lut"])
            stage = st.make_lut_stage(
                lut, label=label,
                retarget_from=entry.get("retarget_from"),
                retarget_to=entry.get("retarget_to"),
                enabled=entry.get("enabled", True),
                opacity=entry.get("opacity", 1.0),
            )
            stage.params["source_kind"] = "embedded"
        else:
            raise ValueError(f"LUT stage {label!r}: no usable source recorded.")
    elif kind == "printer_lights":
        stage = st.make_printer_lights_stage(
            r_points=entry.get("r_points", 0.0),
            g_points=entry.get("g_points", 0.0),
            b_points=entry.get("b_points", 0.0),
            working_colourspace=entry.get("working_colourspace", "ACEScct"),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "aces_drt":
        stage = st.make_aces_drt_stage(
            entry.get("working_colourspace"), entry.get("display"), entry.get("view"),
            config_name=entry.get("config_name"),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "custom_drt":
        stage = st.make_custom_drt_stage(
            entry.get("working_colourspace"),
            contrast=entry.get("contrast", custom_drt.DEFAULT_CONTRAST),
            headroom=entry.get("headroom", custom_drt.DEFAULT_HEADROOM),
            blend_percentile=entry.get("blend_percentile", custom_drt.DEFAULT_BLEND_PERCENTILE),
            tail_width=entry.get("tail_width", custom_drt.DEFAULT_TAIL_WIDTH),
            desat_start=entry.get("desat_start", custom_drt.DEFAULT_DESAT_START),
            desat_end=entry.get("desat_end", custom_drt.DEFAULT_DESAT_END),
            identity_zone=entry.get("identity_zone", custom_drt.DEFAULT_IDENTITY_ZONE),
            transition_softness=entry.get(
                "transition_softness", custom_drt.DEFAULT_TRANSITION_SOFTNESS),
            fullness_r=entry.get("fullness_r", custom_drt.DEFAULT_FULLNESS_R),
            fullness_g=entry.get("fullness_g", custom_drt.DEFAULT_FULLNESS_G),
            fullness_b=entry.get("fullness_b", custom_drt.DEFAULT_FULLNESS_B),
            mastering_display=entry.get(
                "mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY),
            final_target=entry.get("final_target"),
            mastering_brightness=entry.get(
                "mastering_brightness", custom_drt.DEFAULT_MASTERING_BRIGHTNESS),
            config_name=entry.get("config_name"),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "cdl":
        stage = st.make_cdl_stage(
            slope=entry.get("slope", cdl.IDENTITY_SLOPE),
            offset=entry.get("offset", cdl.IDENTITY_OFFSET),
            power=entry.get("power", cdl.IDENTITY_POWER),
            sat=entry.get("sat", cdl.IDENTITY_SAT),
            style=entry.get("style", cdl.DEFAULT_STYLE),
            sop_enabled=entry.get("sop_enabled", True),
            sat_enabled=entry.get("sat_enabled", True),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "colorspace":
        stage = st.make_colorspace_stage(
            entry.get("src"), entry.get("dst"),
            config_name=entry.get("config_name"),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "grading_primary":
        stage = st.make_grading_primary_stage(
            style=entry.get("style", grading_primary.DEFAULT_STYLE),
            brightness=entry.get("brightness", grading_primary.IDENTITY_ADD),
            contrast=entry.get("contrast", grading_primary.IDENTITY_MULT),
            gamma=entry.get("gamma", grading_primary.IDENTITY_MULT),
            offset=entry.get("offset", grading_primary.IDENTITY_ADD),
            exposure=entry.get("exposure", grading_primary.IDENTITY_ADD),
            lift=entry.get("lift", grading_primary.IDENTITY_ADD),
            gain=entry.get("gain", grading_primary.IDENTITY_MULT),
            pivot=entry.get("pivot", grading_primary.DEFAULT_PIVOT),
            saturation=entry.get("saturation", grading_primary.DEFAULT_SATURATION),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "exposure_contrast":
        stage = st.make_exposure_contrast_stage(
            style=entry.get("style", exposure_contrast.DEFAULT_STYLE),
            exposure=entry.get("exposure", exposure_contrast.IDENTITY_EXPOSURE),
            contrast=entry.get("contrast", exposure_contrast.IDENTITY_CONTRAST),
            gamma=entry.get("gamma", exposure_contrast.IDENTITY_GAMMA),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "grading_tone":
        stage = st.make_grading_tone_stage(
            style=entry.get("style", grading_tone.DEFAULT_STYLE),
            blacks=entry.get("blacks"), shadows=entry.get("shadows"),
            midtones=entry.get("midtones"), highlights=entry.get("highlights"),
            whites=entry.get("whites"),
            scontrast=entry.get("scontrast", grading_tone.DEFAULT_SCONTRAST),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "grading_rgb_curve":
        stage = st.make_grading_rgb_curve_stage(
            style=entry.get("style", grading_rgb_curve.DEFAULT_STYLE),
            red=entry.get("red"), green=entry.get("green"),
            blue=entry.get("blue"), master=entry.get("master"),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    elif kind == "fork_split":
        stage = st.make_fork_split_stage(
            label=label,
            enabled=entry.get("enabled", True),
        )
    elif kind == "fork_merge":
        stage = st.make_fork_merge_stage(
            blend=entry.get("blend", 0.5),
            label=label,
            enabled=entry.get("enabled", True),
            opacity=entry.get("opacity", 1.0),
        )
    else:
        raise ValueError(f"unknown kind {kind!r} (from a newer app version?).")
    stage.branch = entry.get("branch")
    return stage


def stack_from_session_dict(data):
    """Inverse of stack_to_session_dict. Returns (stack, warnings) --
    warnings is a list of human-readable strings for any stage that
    couldn't be reconstructed (see stage_from_session_dict for the
    specific failure modes), so the caller can surface them without the
    whole session load failing. A branch-tagged stage (inside a
    fork_split/fork_merge region) that fails is skipped individually,
    same as any other stage -- unlike an earlier single-fork-stage
    design, there's no "whole fork" unit to skip anymore, since
    fork_split/fork_merge/branch stages are all ordinary flat-list
    entries (see this module's docstring, STACK FORKING). One branch
    losing a stage this way doesn't unbalance the region -- _run_stages
    matches split/merge structurally, not by stage count per branch."""
    warnings = []
    stack = st.Stack(
        size=data.get("size", 33),
        domain_min=tuple(data.get("domain_min", (0.0, 0.0, 0.0))),
        domain_max=tuple(data.get("domain_max", (1.0, 1.0, 1.0))),
    )
    for entry in data.get("stages", []):
        label = entry.get("label")
        try:
            stage = stage_from_session_dict(entry)
        except Exception as e:  # noqa: BLE001 -- one bad stage shouldn't sink the whole session
            warnings.append(f"Skipped stage {label!r}: {e}")
            continue
        stack.add_stage(stage)
    return stack, warnings


def memory_library_to_session_list(library):
    """StackMemoryLibrary -> plain JSON-able list, each entry a name plus
    its own stack_to_session_dict -- a memory IS just an independent
    stack, serialized the identical way the working one is. VERSION 10
    (Stills Gallery, Phase 3) additionally writes thumbnail (a
    media_cache.py storage descriptor -- already a plain JSON-able dict,
    or None -- see image_library_to_session_list's identical treatment
    of its own thumbnail field) and full_still (Phase 3 follow-up, per
    Tom: "also capture full canvas-res still" -- a SECOND, same-shaped
    media_cache.py descriptor, see stack_memory.StackMemory's own
    docstring)."""
    return [
        {"name": memory.name, "stack": stack_to_session_dict(memory.stack),
         "thumbnail": memory.thumbnail, "full_still": memory.full_still}
        for memory in library.memories
    ]


def memory_library_from_session_list(entries):
    """Inverse of memory_library_to_session_list. Returns
    (library, warnings) -- a broken memory (bad stage inside it) reports
    its warnings prefixed with the memory's name and is otherwise skipped,
    same "one bad thing doesn't sink everything else" rule
    stack_from_session_dict uses per-stage, just one level up. A
    pre-VERSION-10 file has no thumbnail key; .get("thumbnail") defaults
    it to None, same "old file loads fine, just without the newer field"
    convention as image_library_from_session_list."""
    library = StackMemoryLibrary()
    warnings = []
    for entry in entries:
        name = entry.get("name", "Memory")
        stack, stage_warnings = stack_from_session_dict(entry.get("stack", {}))
        warnings.extend(f'[Memory "{name}"] {msg}' for msg in stage_warnings)
        library.memories.append(StackMemory(
            name, stack, thumbnail=entry.get("thumbnail"),
            full_still=entry.get("full_still")))
    return library, warnings


def gallery_stills_to_session_list(library):
    """GalleryStillLibrary -> plain JSON-able list (Phase 3, Stills
    Gallery) -- same shape as image_library_to_session_list, since
    GalleryStill is structurally the same as ImageLibraryEntry minus the
    video-specific fields (no media_kind/frame metadata -- a gallery
    still is always a still, see stack_memory.GalleryStill's own
    docstring on why it's a distinct class rather than reusing
    ImageLibraryEntry)."""
    return [
        {
            "name": still.name,
            "source_filename": still.source_filename,
            "source_path": still.source_path,
            "thumbnail": still.thumbnail,
        }
        for still in library.stills
        if still.source_path  # nothing to reconstruct from otherwise
    ]


def gallery_stills_from_session_list(entries_data):
    """Inverse of gallery_stills_to_session_list -- returns
    (library, warnings). A GalleryStill has no in-memory pixel data to
    reconstruct (it's thumbnail-only, see that class's docstring), so
    unlike image_library_from_session_list there's no re-decode step and
    thus no way for a MISSING source file to fail here -- source_path is
    kept as-is (the still simply can't be re-imported/re-thumbnailed
    later if the file has moved, same practical caveat
    image_library.py's docstring already documents for its own entries,
    but with no load-time failure mode since nothing is actually read)."""
    library = GalleryStillLibrary()
    warnings = []
    for data in entries_data:
        name = data.get("name", "Reference Still")
        path = data.get("source_path")
        if not path:
            warnings.append(f'Skipped gallery still "{name}": no source path recorded')
            continue
        library.stills.append(GalleryStill(
            name, data.get("thumbnail"), path,
            source_filename=data.get("source_filename"),
        ))
    return library, warnings


def undo_history_to_session_list(undo_history):
    """undo_history.UndoHistory -> plain JSON-able dict of {"states":
    [...], "pointer": int}, each state its own stack_to_session_dict --
    same "it's just an independent stack, serialized the identical way"
    relationship memory_library_to_session_list has to a Stack Memory.
    Capped to the most recent MAX_PERSISTED_UNDO_STATES states (see this
    module's VERSION 8 note on why, unlike the in-memory case, this can't
    be left uncapped) -- states are dropped from the OLDEST end first,
    with `pointer` shifted to match so the current position/redo tail is
    preserved exactly; only how far back undo can reach is reduced."""
    states = undo_history.states
    pointer = undo_history.pointer
    if len(states) > MAX_PERSISTED_UNDO_STATES:
        dropped = len(states) - MAX_PERSISTED_UNDO_STATES
        states = states[dropped:]
        pointer = max(0, pointer - dropped)
    return {
        "states": [stack_to_session_dict(state) for state in states],
        "pointer": pointer,
    }


def undo_history_from_session_list(data):
    """Inverse of undo_history_to_session_list. Returns (states, pointer,
    warnings) where `states` is None if `data` has no usable states (empty/
    missing/malformed -- callers treat that as "this file has no persisted
    history," same as a missing key entirely). A bad stage inside one
    persisted state reports its warnings prefixed with that state's index,
    same per-item "one bad thing doesn't sink everything else" rule
    memory_library_from_session_list uses for Stack Memories."""
    raw_states = data.get("states") or []
    warnings = []
    states = []
    for i, raw_state in enumerate(raw_states):
        stack, stage_warnings = stack_from_session_dict(raw_state)
        warnings.extend(f"[Undo history #{i}] {msg}" for msg in stage_warnings)
        states.append(stack)
    if not states:
        return None, None, warnings
    pointer = data.get("pointer", len(states) - 1)
    return states, pointer, warnings


def cdl_library_to_session_list(library):
    """cdl_library.CDLLibrary -> plain JSON-able list. Unlike
    memory_library_to_session_list, this doesn't delegate to another
    to_session_dict function -- a CDLEntry has no nested Stack/Stage to
    serialize, just its own plain fields (see cdl_library.py's docstring
    on why entries are copied-in values, never file references)."""
    return [
        {
            "name": entry.name,
            "slope": list(entry.slope),
            "offset": list(entry.offset),
            "power": list(entry.power),
            "sat": entry.sat,
            "style": entry.style,
            "sop_enabled": entry.sop_enabled,
            "sat_enabled": entry.sat_enabled,
            "source_filename": entry.source_filename,
            "source_path": entry.source_path,
        }
        for entry in library.entries
    ]


def cdl_library_from_session_list(entries_data):
    """Inverse of cdl_library_to_session_list. No warnings possible here
    (see this module's docstring on VERSION 3) -- every field is a plain
    JSON value with a sensible default, nothing to fail to reconstruct."""
    library = CDLLibrary()
    for data in entries_data:
        library.entries.append(CDLEntry(
            data.get("name", "CDL"),
            data.get("slope", (1.0, 1.0, 1.0)),
            data.get("offset", (0.0, 0.0, 0.0)),
            data.get("power", (1.0, 1.0, 1.0)),
            data.get("sat", 1.0),
            style=data.get("style", "asc"),
            sop_enabled=data.get("sop_enabled", True),
            sat_enabled=data.get("sat_enabled", True),
            source_filename=data.get("source_filename"),
            source_path=data.get("source_path"),
        ))
    return library


def lut_library_to_session_list(library):
    """lut_library.LUTLibrary -> plain JSON-able list. Each entry embeds
    its LUT's full table directly via lut_to_embedded_dict -- see
    lut_library.py's docstring on why a LUT Library entry has no path
    reference to fall back on in the first place, unlike a "lut" stage's
    ordinary source_path."""
    return [
        {
            "name": entry.name,
            "lut": lut_to_embedded_dict(entry.lut),
            "source_filename": entry.source_filename,
            "source_path": entry.source_path,
        }
        for entry in library.entries
    ]


def lut_library_from_session_list(entries_data):
    """Inverse of lut_library_to_session_list. Returns (library,
    warnings) -- unlike cdl_library_from_session_list, this CAN fail
    per-entry (a malformed or future-format table), same "one bad thing
    doesn't sink everything else" rule stack_from_session_dict/
    memory_library_from_session_list use."""
    library = LUTLibrary()
    warnings = []
    for data in entries_data:
        name = data.get("name", "LUT")
        try:
            lut = embedded_dict_to_lut(data["lut"])
        except Exception as e:  # noqa: BLE001 -- one bad entry shouldn't sink the rest
            warnings.append(f'Skipped LUT Library entry "{name}": {e}')
            continue
        library.entries.append(LUTLibraryEntry(
            name, lut,
            source_filename=data.get("source_filename"),
            source_path=data.get("source_path"),
        ))
    return library, warnings


def image_library_to_session_list(library):
    """image_library.ImageLibrary -> plain JSON-able list. UNLIKE
    lut_library_to_session_list, this does NOT embed pixel data -- just
    name/source_path/source_colourspace, re-decoded fresh on load -- see
    this module's docstring (VERSION 5) and image_library.py's own
    docstring for why. VERSION 10 (Media Library Phase 2) additionally
    writes media_kind/frame_count/frame_rate/duration (video metadata,
    ignored/absent for a still entry) and thumbnail (a media_cache.py
    storage descriptor -- already a plain JSON-able dict, see that
    module -- or None), re-generated on load rather than embedded-by-
    value if missing, same "how to rebuild it" principle as the pixel
    data itself."""
    return [
        {
            "name": entry.name,
            "source_colourspace": entry.source_colourspace,
            "source_filename": entry.source_filename,
            "source_path": entry.source_path,
            "media_kind": entry.media_kind,
            "frame_count": entry.frame_count,
            "frame_rate": entry.frame_rate,
            "duration": entry.duration,
            "thumbnail": entry.thumbnail,
        }
        for entry in library.entries
        if entry.source_path  # nothing to reconstruct from otherwise
    ]


def image_library_from_session_list(entries_data):
    """Inverse of image_library_to_session_list. Returns (library,
    warnings) -- an entry whose source file has since moved/been deleted
    can't be reconstructed (unlike a LUT Library entry), so this reports
    a warning and skips it, same "one bad thing doesn't sink everything
    else" rule as every other per-entry failure in this module. A video
    entry (media_kind == "video", VERSION 10+) is reopened via
    video_source.VideoSource instead of load_image_as_array -- same
    "re-decode fresh on load" principle, just the video codepath -- and
    only its first frame is kept in memory, same as import (see
    image_library.py's docstring). A pre-VERSION-10 file has no
    media_kind key at all; .get("media_kind", "still") defaults it to a
    still, same "old file loads fine, just without the newer field"
    convention every prior version bump here already follows."""
    library = ImageLibrary()
    warnings = []
    for data in entries_data:
        name = data.get("name", "Image")
        path = data.get("source_path")
        media_kind = data.get("media_kind", "still")
        try:
            if not path:
                raise ValueError("no source path recorded")
            if media_kind == "video":
                source = video_source.VideoSource(path)
                try:
                    array = source.decode_next_frame()
                finally:
                    source.close()
            else:
                array = load_image_as_array(path, max_dimension=None)
        except Exception as e:  # noqa: BLE001 -- one bad entry shouldn't sink the rest
            warnings.append(f'Skipped Image Library entry "{name}": {e}')
            continue
        library.entries.append(ImageLibraryEntry(
            name, array,
            source_colourspace=data.get("source_colourspace"),
            source_filename=data.get("source_filename"),
            source_path=path,
            media_kind=media_kind,
            frame_count=data.get("frame_count"),
            frame_rate=data.get("frame_rate"),
            duration=data.get("duration"),
            thumbnail=data.get("thumbnail"),
        ))
    return library, warnings


def save_session(path, stack, sample_image_state, stack_memory_library=None,
                  gallery_still_library=None, cdl_library=None, lut_library=None,
                  image_library=None, undo_history=None, canvas=None):
    """sample_image_state: whatever SampleImagePanel.get_session_state()
    returned (a dict, or None if no image is currently loaded).
    stack_memory_library: whatever StackMemoriesPanel.get_library()
    returned (a StackMemoryLibrary, or None to save with no memories --
    e.g. for callers that don't have one). gallery_still_library: whatever
    StackMemoriesPanel.get_gallery_library() returned (a
    stack_memory.GalleryStillLibrary, Phase 3 Stills Gallery, or None to
    save with no gallery stills). cdl_library: whatever
    CDLLibraryPanel.get_library() returned (a cdl_library.CDLLibrary, or
    None to save with no CDL Library entries). lut_library: whatever
    LUTLibraryPanel.get_library() returned (a lut_library.LUTLibrary, or
    None to save with no LUT Library entries). image_library: whatever
    ImageLibraryPanel.get_library() returned (an image_library.ImageLibrary,
    or None to save with no Image Library entries). undo_history: the
    MainWindow's undo_history.UndoHistory for the working Stack, or None
    to save with no persisted undo history (e.g. for callers -- tests,
    Stack Memory export -- that have no undo history of their own; see
    this module's VERSION 8 note). canvas: a canvas.CanvasSettings, or
    None to save with no canvas section at all (loads back as
    canvas.DEFAULT_CANVAS -- see VERSION 9 note).

    No `workspace` parameter as of VERSION 7 (see this module's docstring)
    -- workspace layout is saved/loaded independently now, via
    workspace_io.py, not as part of a session at all."""
    data = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "stack": stack_to_session_dict(stack),
        "sample_image": sample_image_state,
        "stack_memories": (
            memory_library_to_session_list(stack_memory_library)
            if stack_memory_library is not None else []
        ),
        "gallery_stills": (
            gallery_stills_to_session_list(gallery_still_library)
            if gallery_still_library is not None else []
        ),
        "cdl_library": (
            cdl_library_to_session_list(cdl_library)
            if cdl_library is not None else []
        ),
        "lut_library": (
            lut_library_to_session_list(lut_library)
            if lut_library is not None else []
        ),
        "image_library": (
            image_library_to_session_list(image_library)
            if image_library is not None else []
        ),
        "undo_history": (
            undo_history_to_session_list(undo_history)
            if undo_history is not None else None
        ),
        "canvas": (
            cv.canvas_to_session_dict(canvas)
            if canvas is not None else None
        ),
    }
    with atomic_open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_session(path):
    """Returns (stack, sample_image_state_or_None, stack_memory_library,
    gallery_still_library, cdl_library, lut_library, image_library,
    undo_history_state, canvas, warnings).
    Raises ValueError if `path` isn't a LUTier session file at all (wrong
    format/not JSON) -- that's a hard failure, unlike a single
    unreconstructable stage/memory/LUT Library/Image Library entry. Files
    saved before Stack Memories existed (version 1), before the CDL
    Library existed (version 1/2), before the LUT Library existed
    (version 1/2/3), or before the Image Library/Reference image existed
    (version 1-4) load fine with an empty memory library/CDL Library/LUT
    Library/Image Library (and no Reference image), via the
    .get("stack_memories", [])/.get("cdl_library", [])/
    .get("lut_library", [])/.get("image_library", []) defaults below.

    undo_history_state is None for any file with no persisted undo
    history -- version < 8, or a version 8+ file saved with
    undo_history=None -- callers (MainWindow) treat that as "start a
    fresh history for this newly loaded stack" exactly like New Session.
    When present it's a (states, pointer) pair ready for
    undo_history.UndoHistory.from_states(*undo_history_state).

    No `workspace` element as of VERSION 7 (see this module's docstring)
    -- a VERSION 6 file's leftover "workspace" key, if present, is simply
    never read here; see workspace_io.py for where workspace layout
    actually lives now.

    canvas is a canvas.CanvasSettings -- canvas.DEFAULT_CANVAS for any
    file with no "canvas" key (version < 9, or a version 9+ file saved
    with canvas=None), same convention every prior version bump follows
    (see VERSION 9 note above)."""
    with open(path, "r") as f:
        data = json.load(f)
    if data.get("format") != SESSION_FORMAT:
        raise ValueError(f"Not a LUTier session file: {path}")
    stack, warnings = stack_from_session_dict(data.get("stack", {}))
    memory_library, memory_warnings = memory_library_from_session_list(
        data.get("stack_memories", []))
    warnings = warnings + memory_warnings
    gallery_still_library, gallery_still_warnings = gallery_stills_from_session_list(
        data.get("gallery_stills", []))
    warnings = warnings + gallery_still_warnings
    cdl_library = cdl_library_from_session_list(data.get("cdl_library", []))
    lut_library, lut_library_warnings = lut_library_from_session_list(
        data.get("lut_library", []))
    warnings = warnings + lut_library_warnings
    image_library, image_library_warnings = image_library_from_session_list(
        data.get("image_library", []))
    warnings = warnings + image_library_warnings
    sample_image_state = data.get("sample_image")

    undo_history_state = None
    undo_data = data.get("undo_history")
    if undo_data:
        undo_states, undo_pointer, undo_warnings = undo_history_from_session_list(undo_data)
        warnings = warnings + undo_warnings
        if undo_states is not None:
            undo_history_state = (undo_states, undo_pointer)

    canvas = cv.canvas_from_session_dict(data.get("canvas"))

    return (stack, sample_image_state, memory_library, gallery_still_library,
            cdl_library, lut_library, image_library, undo_history_state, canvas,
            warnings)
